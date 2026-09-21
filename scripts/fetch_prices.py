#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시세 수집기: Yahoo Finance(yfinance) -> Supabase

  python scripts/fetch_prices.py                # 지금 장이 열린 시장만 갱신 (매시간 자동 실행용)
  python scripts/fetch_prices.py --mode all     # 모든 시장 갱신
  python scripts/fetch_prices.py --mode backfill  # 모든 종목의 과거 1년 일봉까지 채움 (최초 1회)
  python scripts/fetch_prices.py --mode verify  # 종목코드가 맞는지 야후 종목명과 대조 (DB는 건드리지 않음)

환경변수
  SUPABASE_URL         예) https://abcdefgh.supabase.co
  SUPABASE_SECRET_KEY  Supabase secret key (sb_secret_...). 예전 service_role 키(eyJ...)도 동작합니다.
"""
import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TICKERS_CSV = ROOT / "data" / "tickers.csv"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()

CHUNK = 40            # 야후에 한 번에 요청할 종목 수
CHUNK_PAUSE = 1.5     # 요청 묶음 사이 쉬는 시간(초)
FX_SYMBOLS = ["KRW=X", "CNYKRW=X", "HKDKRW=X", "CNY=X", "HKD=X"]
FX_RANGE = {"USD": (700, 3000), "CNY": (90, 450), "HKD": (90, 400)}   # 이상치 방어용 허용 범위


def log(*a):
    print(dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


# ----------------------------------------------------------------- Supabase REST

def _headers(extra=None):
    h = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    # 새 형식 키(sb_...)는 apikey 헤더로만 보낸다. 예전 JWT 키는 Authorization 헤더도 필요.
    if not SUPABASE_KEY.startswith("sb_"):
        h["Authorization"] = "Bearer " + SUPABASE_KEY
    if extra:
        h.update(extra)
    return h


def rest(method, path, params=None, body=None, prefer=None, tries=3):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    extra = {"Prefer": prefer} if prefer else None
    data = None if body is None else json.dumps(body, allow_nan=False, ensure_ascii=False).encode("utf-8")
    last = None
    for i in range(tries):
        try:
            r = requests.request(method, url, params=params, data=data, headers=_headers(extra), timeout=60)
            if r.status_code < 300:
                return r.json() if r.text.strip() else None
            last = f"{r.status_code} {r.text[:300]}"
            if r.status_code < 500 and r.status_code != 429:
                break   # 우리 쪽 요청이 잘못된 경우: 다시 보내도 같다
        except requests.RequestException as e:   # 네트워크 오류는 재시도
            last = repr(e)
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"Supabase {method} {path} 실패: {last}")


def upsert(table, rows, on_conflict):
    for i in range(0, len(rows), 500):
        rest("POST", table, params={"on_conflict": on_conflict}, body=rows[i:i + 500],
             prefer="resolution=merge-duplicates,return=minimal")


def select(table, params):
    return rest("GET", table, params=params) or []


# ----------------------------------------------------------------- 종목 목록

def load_tickers():
    with open(TICKERS_CSV, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]
    out = []
    for i, r in enumerate(rows):
        out.append({
            "ticker": r["ticker"].strip(),
            "code": r["code"].strip(),
            "name_ko": r["name_ko"].strip(),
            "name_en": (r.get("name_en") or "").strip() or None,
            "market": r["market"].strip().upper(),
            "exchange": (r.get("exchange") or "").strip() or None,
            "country": r["country"].strip().upper(),
            "currency": r["currency"].strip().upper(),
            "tradable": (r.get("tradable") or "1").strip() not in ("0", "false", "False", ""),
            "active": True,
            "sort_order": i,
        })
    seen = set()
    for r in out:
        if r["ticker"] in seen:
            raise SystemExit(f"tickers.csv 에 중복된 ticker 가 있습니다: {r['ticker']}")
        seen.add(r["ticker"])
    return out


def markets_due(now_utc, mode):
    """지금 갱신할 시장(KR/CN/US). 장중에는 매시간, 장 마감 뒤에는 종가 확정용으로 한 번 더."""
    if mode in ("all", "backfill"):
        return {"KR", "CN", "US"}
    due = set()
    if now_utc.weekday() < 5:                      # 월~금 (UTC 기준)
        hm = now_utc.hour + now_utc.minute / 60
        if 0 <= hm < 7.5:                          # 한국 09:00~15:30 KST
            due.add("KR")
        if 1 <= hm < 9:                            # 본토 10:30~16:00 KST, 홍콩 ~17:00 KST
            due.add("CN")
        if 13 <= hm < 22:                          # 미국 정규장 (서머타임/표준시 모두 포함)
            due.add("US")
        if now_utc.hour in (9, 22):                # 하루 두 번 전체 갱신 (종가·누락 보정)
            due |= {"KR", "CN", "US"}
    return due


# ----------------------------------------------------------------- 야후 파이낸스

def _yf():
    import yfinance as yf   # 지연 import: verify/테스트 환경 배려
    return yf


def yf_download(symbols, period):
    import pandas as pd
    yf = _yf()
    for attempt in range(3):
        try:
            df = yf.download(tickers=symbols, period=period, interval="1d", group_by="ticker",
                             auto_adjust=False, actions=True, threads=True, progress=False)
            if df is not None and len(df) > 0:
                return df
        except Exception as e:  # noqa: BLE001  (야후 쪽 오류는 종류가 다양하다)
            log("  야후 요청 오류:", repr(e)[:200])
        time.sleep(20 * (attempt + 1))
    return pd.DataFrame()


def frame_for(df, sym):
    """다중 종목 DataFrame에서 한 종목의 (Close 가 있는) 행만 꺼낸다."""
    import pandas as pd
    if df is None or len(df) == 0:
        return None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            if sym not in df.columns.get_level_values(0):
                return None
            sub = df[sym]
        else:
            sub = df
        if "Close" not in sub.columns:
            return None
        sub = sub.dropna(subset=["Close"])
        return sub if len(sub) else None
    except Exception:  # noqa: BLE001
        return None


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def fetch_group(symbols, period):
    """{symbol: DataFrame}. 실패한 심볼은 빠진다."""
    got = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        df = yf_download(chunk, period)
        for s in chunk:
            sub = frame_for(df, s)
            if sub is not None:
                got[s] = sub
        time.sleep(CHUNK_PAUSE)
    return got


def alt_korean_symbol(sym):
    """코스닥<->코스피 이전상장에 대비: .KQ 가 안 되면 .KS 로 (반대도) 시도."""
    if sym.endswith(".KQ"):
        return sym[:-3] + ".KS"
    if sym.endswith(".KS"):
        return sym[:-3] + ".KQ"
    return None


def fetch_with_fallback(tickers, period):
    got = fetch_group(tickers, period)
    missing = [t for t in tickers if t not in got]
    if missing:
        time.sleep(5)
        retry = fetch_group(missing, period)             # 한 번 더
        got.update(retry)
        still = [t for t in missing if t not in got]
        alts = {alt_korean_symbol(t): t for t in still if alt_korean_symbol(t)}
        if alts:
            alt_got = fetch_group(list(alts.keys()), period)
            for alt_sym, sub in alt_got.items():
                orig = alts[alt_sym]
                got[orig] = sub
                log(f"  참고: {orig} 는 {alt_sym} 로 조회되었습니다 (이전상장?). tickers.csv 수정을 권장합니다.")
    return got


def fetch_fx(period):
    """{'USD': {date: rate}, 'CNY': {...}, 'HKD': {...}}  (1 외화 = ? 원)"""
    got = fetch_group(FX_SYMBOLS, period)

    def series(sym):
        sub = got.get(sym)
        if sub is None:
            return {}
        return {idx.date(): num(v) for idx, v in sub["Close"].items() if num(v)}

    usdkrw = series("KRW=X")
    out = {"USD": usdkrw}
    for ccy, direct, via in (("CNY", "CNYKRW=X", "CNY=X"), ("HKD", "HKDKRW=X", "HKD=X")):
        s = series(direct)
        if not s:                                    # 직접 환율이 없으면 달러를 거쳐 계산
            usd_ccy = series(via)
            s = {d: usdkrw[d] / usd_ccy[d] for d in usdkrw if d in usd_ccy and usd_ccy[d]}
        out[ccy] = s
    for ccy, (lo, hi) in FX_RANGE.items():
        out[ccy] = {d: round(v, 4) for d, v in out[ccy].items() if lo < v < hi}
    return out


# ----------------------------------------------------------------- 액면분할

def detect_splits(frames):
    found = []
    for t, sub in frames.items():
        if "Stock Splits" not in sub.columns:
            continue
        for idx, v in sub["Stock Splits"].items():
            r = num(v)
            if r and abs(r - 1) > 1e-9 and 0.02 <= r <= 50:
                found.append((t, idx.date(), r))
    return found


def handle_splits(frames, today):
    splits = [s for s in detect_splits(frames) if (today - s[1]).days <= 10]
    if not splits:
        return []
    since = (today - dt.timedelta(days=40)).isoformat()
    known = {(r["ticker"], r["action_date"]) for r in
             select("corporate_actions", {"select": "ticker,action_date", "action_date": f"gte.{since}"})}
    applied = []
    for t, d, ratio in splits:
        if (t, d.isoformat()) in known:
            continue
        note, ok = "no stored pre-split price; nothing to adjust", False
        prev = select("prices_daily", {"select": "d,close", "ticker": f"eq.{t}", "d": f"lt.{d.isoformat()}",
                                       "order": "d.desc", "limit": "1"})
        if prev:
            pd_date = dt.date.fromisoformat(prev[0]["d"])
            stored = num(prev[0]["close"])
            sub = frames[t]
            adj = [num(v) for idx, v in sub["Close"].items() if idx.date() == pd_date]
            if stored and adj and adj[0]:
                observed = stored / adj[0]            # 저장값(분할 전) / 야후 수정주가(분할 후) ≈ 분할비율
                if abs(observed / ratio - 1) < 0.2:
                    n = rest("POST", "rpc/apply_split",
                             body={"p_ticker": t, "p_date": d.isoformat(), "p_ratio": ratio})
                    log(f"  액면분할 반영: {t} {d} x{ratio} (보유계좌 {n}건 조정)")
                    applied.append(t)
                    ok = True
                elif abs(observed - 1) < 0.05:
                    note = "stored prices already split-adjusted"
                else:
                    note = f"ratio mismatch (observed {observed:.3f}); needs manual check"
                    log(f"  경고: {t} 분할비율 확인 필요 - 야후 {ratio}, 관측 {observed:.3f}")
        if not ok:
            upsert("corporate_actions", [{"ticker": t, "action_date": d.isoformat(), "kind": "SPLIT",
                                          "ratio": ratio, "applied": False, "note": note}],
                   "ticker,action_date,kind")
    return applied


# ----------------------------------------------------------------- 메인 작업

def daily_rows(ticker, sub):
    rows = []
    for idx, v in sub["Close"].items():
        c = num(v)
        if c:
            rows.append({"ticker": ticker, "d": idx.date().isoformat(), "close": round(c, 6)})
    return rows


def run(mode):
    import pandas as pd  # noqa: F401  (yfinance 가 요구)
    t0 = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    stocks = load_tickers()
    by_ticker = {s["ticker"]: s for s in stocks}

    # 1) 종목 마스터 동기화
    upsert("stocks", [dict(s, updated_at=now.isoformat()) for s in stocks], "ticker")
    rest("POST", "rpc/deactivate_missing", body={"p_tickers": list(by_ticker)})

    prev = {r["ticker"]: r for r in select("prices_latest", {"select": "ticker,price,price_date"})}
    first_run = len(prev) == 0
    if first_run and mode != "backfill":
        log("저장된 시세가 없습니다 → 최초 실행으로 보고 backfill 모드로 전환합니다.")
        mode = "backfill"

    due = markets_due(now, mode)
    log(f"mode={mode} 갱신 대상 시장={sorted(due) or '없음(장 시간 아님)'}")

    ok, failed = 0, []
    if due:
        period = "1y" if mode == "backfill" else "5d"
        targets = [s["ticker"] for s in stocks if s["country"] in due]
        new_ones = [t for t in targets if t not in prev] if mode != "backfill" else []

        frames = fetch_with_fallback(targets, period)
        if new_ones:                                   # 새로 추가된 종목은 과거 1년치도 채운다
            log(f"새 종목 {len(new_ones)}개 과거 데이터 채우는 중")
            frames.update(fetch_with_fallback(new_ones, "1y"))

        try:
            resplit = handle_splits(frames, today)
            if resplit:                                # 분할된 종목은 수정주가로 과거 일봉을 다시 채운다
                frames.update(fetch_with_fallback(resplit, "1y"))
        except Exception as e:  # noqa: BLE001
            log("  액면분할 처리 중 오류(시세 갱신은 계속):", repr(e)[:300])

        latest, hourly, daily = [], [], []
        ts_hour = now.replace(minute=0, second=0, microsecond=0).isoformat()
        for t in targets:
            sub = frames.get(t)
            if sub is None:
                failed.append(t)
                continue
            closes = [(idx.date(), num(v)) for idx, v in sub["Close"].items() if num(v)]
            if not closes:
                failed.append(t)
                continue
            pdate, price = closes[-1]
            prev_close = closes[-2][1] if len(closes) > 1 else None
            latest.append({"ticker": t, "price": round(price, 6),
                           "prev_close": round(prev_close, 6) if prev_close else None,
                           "price_date": pdate.isoformat(), "updated_at": now.isoformat()})
            p = prev.get(t)
            if p is None or float(p["price"]) != round(price, 6) or p["price_date"] != pdate.isoformat():
                hourly.append({"ticker": t, "ts": ts_hour, "price": round(price, 6),
                               "price_date": pdate.isoformat()})
            daily.extend(daily_rows(t, sub))
            ok += 1

        upsert("prices_latest", latest, "ticker")
        upsert("prices_hourly", hourly, "ticker,ts")
        upsert("prices_daily", daily, "ticker,d")
        log(f"시세 저장: 최신 {len(latest)} / 시간별 {len(hourly)} / 일봉 {len(daily)} 행, 실패 {len(failed)}")
        if failed:
            log("  시세를 못 받은 종목:", ", ".join(failed[:60]), "..." if len(failed) > 60 else "")

    # 2) 환율 (주말 제외 매번)
    if due or mode != "normal":
        fx = fetch_fx("1y" if mode == "backfill" else "5d")
        fx_latest, fx_daily = [], []
        for ccy, s in fx.items():
            if not s:
                log(f"  경고: {ccy} 환율을 받지 못했습니다")
                continue
            last_d = max(s)
            fx_latest.append({"currency": ccy, "krw_rate": s[last_d], "updated_at": now.isoformat()})
            fx_daily.extend({"currency": ccy, "d": d.isoformat(), "krw_rate": v} for d, v in s.items())
        upsert("fx_latest", fx_latest, "currency")
        upsert("fx_daily", fx_daily, "currency,d")
        log("환율 저장:", ", ".join(f"{r['currency']}={r['krw_rate']}" for r in fx_latest) or "없음")

    # 3) 실행 기록 (앱 화면의 '시세 갱신 시각'에 쓰임) + 오래된 기록 정리
    rest("POST", "job_runs", body=[{"mode": mode, "markets": ",".join(sorted(due)), "ok_count": ok,
                                    "fail_count": len(failed), "failed": ",".join(failed)[:2000] or None,
                                    "duration_s": round(time.time() - t0, 1)}],
         prefer="return=minimal")
    cutoff = (now - dt.timedelta(days=14)).isoformat()
    rest("DELETE", "job_runs", params={"ran_at": f"lt.{cutoff}"}, prefer="return=minimal")

    if due and ok == 0:
        log("모든 종목의 시세 수집에 실패했습니다. (야후 차단/장애 가능성) → yfinance 최신 버전인지 확인하세요.")
        return 1
    return 0


def verify():
    """tickers.csv 의 코드가 맞는지 야후가 알려주는 종목명과 나란히 출력한다."""
    yf = _yf()
    stocks = load_tickers()
    out = ROOT / "verify_report.csv"
    bad = 0
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name_ko", "name_en(csv)", "yahoo_name", "yahoo_currency", "status"])
        for s in stocks:
            name, ccy, status = "", "", "OK"
            try:
                info = yf.Ticker(s["ticker"]).info or {}
                name = info.get("shortName") or info.get("longName") or ""
                ccy = info.get("currency") or ""
                if not name:
                    status = "NO DATA"
                elif ccy and ccy != s["currency"]:
                    status = "CURRENCY MISMATCH"
            except Exception as e:  # noqa: BLE001
                status = "ERROR " + repr(e)[:80]
            if status != "OK":
                bad += 1
            w.writerow([s["ticker"], s["name_ko"], s["name_en"] or "", name, ccy, status])
            print(f"{s['ticker']:<12} {s['name_ko']:<20} | {name:<40} {ccy:<4} {status}", flush=True)
            time.sleep(0.6)
    print(f"\n확인 필요 {bad}건 / 전체 {len(stocks)}건 → {out.name} 에 저장했습니다.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="normal", choices=["normal", "all", "backfill", "verify"])
    args = ap.parse_args()
    if args.mode == "verify":
        return verify()
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("환경변수 SUPABASE_URL / SUPABASE_SECRET_KEY 가 필요합니다.")
    return run(args.mode)


if __name__ == "__main__":
    sys.exit(main())
