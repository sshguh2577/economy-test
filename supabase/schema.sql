-- =====================================================================
--  모의투자 원장 · Supabase 스키마
--  Supabase 대시보드 > SQL Editor 에 전체를 붙여넣고 한 번 실행하세요.
--  (여러 번 실행해도 안전하도록 작성되어 있습니다)
-- =====================================================================

-- ---------- 1. 시세·종목 테이블 (누구나 읽기, 쓰기는 수집 스크립트만) ----------

create table if not exists public.stocks (
  ticker      text primary key,              -- 야후 파이낸스 심볼 (예: 005930.KS, NVDA, 0700.HK)
  code        text not null,                 -- 화면에 보여줄 종목코드
  name_ko     text not null,
  name_en     text,
  market      text not null check (market in ('KOSPI','KOSDAQ','NASDAQ','CHINA','INDEX')),
  exchange    text,
  country     text not null check (country in ('KR','US','CN')),
  currency    text not null check (currency in ('KRW','USD','CNY','HKD')),
  tradable    boolean not null default true, -- 지수(INDEX)는 false
  active      boolean not null default true,
  sort_order  int not null default 0,
  updated_at  timestamptz not null default now()
);

create table if not exists public.prices_latest (
  ticker      text primary key references public.stocks(ticker) on delete cascade,
  price       numeric not null,
  prev_close  numeric,
  price_date  date not null,                 -- 이 가격이 속한 거래일(해당 시장 현지 날짜)
  updated_at  timestamptz not null default now()
);

create table if not exists public.prices_hourly (
  ticker      text not null references public.stocks(ticker) on delete cascade,
  ts          timestamptz not null,
  price       numeric not null,
  price_date  date not null,
  primary key (ticker, ts)
);

create table if not exists public.prices_daily (
  ticker      text not null references public.stocks(ticker) on delete cascade,
  d           date not null,
  close       numeric not null,
  primary key (ticker, d)
);

create table if not exists public.fx_latest (
  currency    text primary key check (currency in ('USD','CNY','HKD')),
  krw_rate    numeric not null,              -- 1 외화 = ? 원
  updated_at  timestamptz not null default now()
);

create table if not exists public.fx_daily (
  currency    text not null check (currency in ('USD','CNY','HKD')),
  d           date not null,
  krw_rate    numeric not null,
  primary key (currency, d)
);

create table if not exists public.corporate_actions (
  id          bigint generated always as identity primary key,
  ticker      text not null references public.stocks(ticker) on delete cascade,
  action_date date not null,
  kind        text not null default 'SPLIT',
  ratio       numeric not null,              -- 2 = 1주가 2주로 (액면분할), 0.1 = 10주가 1주로 (병합)
  applied     boolean not null default false,
  note        text,
  created_at  timestamptz not null default now(),
  unique (ticker, action_date, kind)
);

create table if not exists public.job_runs (
  id          bigint generated always as identity primary key,
  ran_at      timestamptz not null default now(),
  mode        text,
  markets     text,
  ok_count    int not null default 0,
  fail_count  int not null default 0,
  failed      text,
  duration_s  numeric
);

-- ---------- 2. 사용자 데이터 ----------

create table if not exists public.profiles (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  nickname    text not null check (char_length(nickname) between 1 and 20),
  created_at  timestamptz not null default now()
);

create table if not exists public.simulations (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  start_date   date not null,
  end_date     date not null,
  initial_cash numeric not null default 100000000,
  cash         numeric not null,
  status       text not null default 'active' check (status in ('active','archived')),
  created_at   timestamptz not null default now(),
  check (end_date > start_date)
);
-- 한 사람당 진행 중인 모의투자는 하나만
create unique index if not exists simulations_one_active_per_user
  on public.simulations(user_id) where status = 'active';

create table if not exists public.holdings (
  simulation_id uuid not null references public.simulations(id) on delete cascade,
  ticker        text not null references public.stocks(ticker),
  quantity      numeric not null check (quantity > 0),
  avg_price     numeric not null,            -- 평균 매입단가 (현지 통화)
  avg_cost_krw  numeric not null,            -- 1주당 평균 매입원가 (원화, 매입 시점 환율 반영)
  primary key (simulation_id, ticker)
);

create table if not exists public.trades (
  id               bigint generated always as identity primary key,
  simulation_id    uuid not null references public.simulations(id) on delete cascade,
  user_id          uuid not null references auth.users(id) on delete cascade,
  ticker           text not null references public.stocks(ticker),
  side             text not null check (side in ('BUY','SELL')),
  quantity         numeric not null check (quantity > 0),
  price            numeric not null,         -- 체결가 (현지 통화)
  currency         text not null,
  fx_rate          numeric not null,         -- 체결 시점 환율 (원화 종목은 1)
  amount_krw       numeric not null,         -- 체결 금액 (원)
  realized_pnl_krw numeric,                  -- 매도일 때만: 실현손익 (원)
  price_date       date not null,            -- 체결에 쓰인 시세의 거래일
  price_updated_at timestamptz,              -- 체결에 쓰인 시세가 수집된 시각
  executed_at      timestamptz not null default now()
);
create index if not exists trades_sim_idx on public.trades(simulation_id, executed_at);

-- ---------- 3. 권한(GRANT) + 행 수준 보안(RLS) ----------

grant usage on schema public to anon, authenticated, service_role;

-- 먼저 기본 권한을 모두 걷어낸 뒤 필요한 것만 다시 부여합니다.
revoke all on public.stocks, public.prices_latest, public.prices_hourly, public.prices_daily,
              public.fx_latest, public.fx_daily, public.corporate_actions, public.job_runs,
              public.profiles, public.simulations, public.holdings, public.trades
  from anon, authenticated;

-- 로그인 사용자: 시세류는 읽기만, 계좌 테이블도 읽기만 (변경은 아래 함수로만 가능)
grant select on public.stocks, public.prices_latest, public.prices_hourly, public.prices_daily,
                public.fx_latest, public.fx_daily, public.corporate_actions, public.job_runs,
                public.simulations, public.holdings, public.trades
  to authenticated;
grant select, insert, update on public.profiles to authenticated;

-- 수집 스크립트(secret key = service_role)
grant all on public.stocks, public.prices_latest, public.prices_hourly, public.prices_daily,
             public.fx_latest, public.fx_daily, public.corporate_actions, public.job_runs,
             public.profiles, public.simulations, public.holdings, public.trades
  to service_role;
grant usage, select on all sequences in schema public to service_role;

alter table public.stocks            enable row level security;
alter table public.prices_latest     enable row level security;
alter table public.prices_hourly     enable row level security;
alter table public.prices_daily      enable row level security;
alter table public.fx_latest         enable row level security;
alter table public.fx_daily          enable row level security;
alter table public.corporate_actions enable row level security;
alter table public.job_runs          enable row level security;
alter table public.profiles          enable row level security;
alter table public.simulations       enable row level security;
alter table public.holdings          enable row level security;
alter table public.trades            enable row level security;

-- 시세류: 로그인한 사용자 누구나 읽기
drop policy if exists "read stocks" on public.stocks;
create policy "read stocks" on public.stocks for select to authenticated using (true);
drop policy if exists "read prices_latest" on public.prices_latest;
create policy "read prices_latest" on public.prices_latest for select to authenticated using (true);
drop policy if exists "read prices_hourly" on public.prices_hourly;
create policy "read prices_hourly" on public.prices_hourly for select to authenticated using (true);
drop policy if exists "read prices_daily" on public.prices_daily;
create policy "read prices_daily" on public.prices_daily for select to authenticated using (true);
drop policy if exists "read fx_latest" on public.fx_latest;
create policy "read fx_latest" on public.fx_latest for select to authenticated using (true);
drop policy if exists "read fx_daily" on public.fx_daily;
create policy "read fx_daily" on public.fx_daily for select to authenticated using (true);
drop policy if exists "read corporate_actions" on public.corporate_actions;
create policy "read corporate_actions" on public.corporate_actions for select to authenticated using (true);
drop policy if exists "read job_runs" on public.job_runs;
create policy "read job_runs" on public.job_runs for select to authenticated using (true);

-- 프로필: 본인 것만
drop policy if exists "own profile select" on public.profiles;
create policy "own profile select" on public.profiles for select to authenticated
  using (user_id = (select auth.uid()));
drop policy if exists "own profile insert" on public.profiles;
create policy "own profile insert" on public.profiles for insert to authenticated
  with check (user_id = (select auth.uid()));
drop policy if exists "own profile update" on public.profiles;
create policy "own profile update" on public.profiles for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

-- 계좌: 본인 것만 읽기 (쓰기 정책 없음 = 직접 쓰기 불가)
drop policy if exists "own simulations" on public.simulations;
create policy "own simulations" on public.simulations for select to authenticated
  using (user_id = (select auth.uid()));
drop policy if exists "own holdings" on public.holdings;
create policy "own holdings" on public.holdings for select to authenticated
  using (exists (select 1 from public.simulations s
                 where s.id = holdings.simulation_id and s.user_id = (select auth.uid())));
drop policy if exists "own trades" on public.trades;
create policy "own trades" on public.trades for select to authenticated
  using (user_id = (select auth.uid()));

-- ---------- 4. 함수 ----------

create or replace function public.kst_today() returns date
language sql stable
set search_path = public
as $$ select (now() at time zone 'Asia/Seoul')::date $$;

-- 모의투자 시작: 시작일·종료일을 정하고 1억 원으로 계좌를 만든다
create or replace function public.create_simulation(p_start date, p_end date)
returns public.simulations
language plpgsql security definer
set search_path = public
as $$
declare
  v_uid uuid := auth.uid();
  v_row public.simulations;
begin
  if v_uid is null then raise exception '로그인이 필요합니다'; end if;
  if p_start is null or p_end is null then raise exception '시작일과 종료일을 모두 입력하세요'; end if;
  if p_start < public.kst_today() then raise exception '시작일은 오늘이거나 오늘 이후여야 합니다'; end if;
  if p_end <= p_start then raise exception '종료일은 시작일보다 뒤여야 합니다'; end if;
  if p_end > p_start + 366 then raise exception '기간은 최대 1년까지 설정할 수 있습니다'; end if;
  if exists (select 1 from public.simulations where user_id = v_uid and status = 'active') then
    raise exception '이미 진행 중인 모의투자가 있습니다';
  end if;
  insert into public.simulations (user_id, start_date, end_date, initial_cash, cash)
  values (v_uid, p_start, p_end, 100000000, 100000000)
  returning * into v_row;
  return v_row;
end $$;

-- 모의투자 초기화: 현재 계좌를 보관 처리(기록은 남김)하고 새로 시작할 수 있게 한다
create or replace function public.archive_simulation()
returns void
language plpgsql security definer
set search_path = public
as $$
declare v_uid uuid := auth.uid();
begin
  if v_uid is null then raise exception '로그인이 필요합니다'; end if;
  update public.simulations set status = 'archived' where user_id = v_uid and status = 'active';
end $$;

-- 매수/매도 체결: 가격은 항상 서버에 저장된 최신 시세를 사용한다 (클라이언트가 가격을 보낼 수 없음)
create or replace function public.execute_trade(p_ticker text, p_side text, p_quantity numeric)
returns json
language plpgsql security definer
set search_path = public
as $$
declare
  v_uid      uuid := auth.uid();
  v_today    date := public.kst_today();
  v_side     text := upper(coalesce(p_side, ''));
  v_sim      public.simulations;
  v_stock    public.stocks;
  v_px       public.prices_latest;
  v_hold     public.holdings;
  v_fx       numeric := 1;
  v_qty      numeric;
  v_amount   numeric;
  v_unit_krw numeric;
  v_realized numeric := null;
begin
  if v_uid is null then raise exception '로그인이 필요합니다'; end if;
  if v_side not in ('BUY', 'SELL') then raise exception '주문 종류가 올바르지 않습니다'; end if;

  select * into v_sim from public.simulations
   where user_id = v_uid and status = 'active' for update;
  if not found then raise exception '진행 중인 모의투자가 없습니다'; end if;
  if v_today < v_sim.start_date then raise exception '아직 시작일 전입니다 (시작일: %)', v_sim.start_date; end if;
  if v_today > v_sim.end_date then raise exception '종료된 모의투자입니다 (종료일: %)', v_sim.end_date; end if;

  select * into v_stock from public.stocks where ticker = p_ticker and active and tradable;
  if not found then raise exception '거래할 수 없는 종목입니다'; end if;

  select * into v_px from public.prices_latest where ticker = p_ticker;
  if not found or v_px.price is null or v_px.price <= 0 then
    raise exception '아직 시세가 수집되지 않은 종목입니다';
  end if;

  if v_stock.currency <> 'KRW' then
    select krw_rate into v_fx from public.fx_latest where currency = v_stock.currency;
    if v_fx is null or v_fx <= 0 then raise exception '환율 정보가 아직 없습니다'; end if;
  end if;

  v_qty := round(p_quantity, 4);
  if v_qty is null or v_qty <= 0 then raise exception '수량은 0보다 커야 합니다'; end if;
  if v_qty > 100000000 then raise exception '수량이 너무 큽니다'; end if;

  v_unit_krw := v_px.price * v_fx;
  v_amount   := round(v_qty * v_unit_krw);

  if v_side = 'BUY' then
    if v_amount > v_sim.cash then
      raise exception '현금이 부족합니다 (필요 %원, 보유 %원)', v_amount, round(v_sim.cash);
    end if;
    insert into public.holdings as h (simulation_id, ticker, quantity, avg_price, avg_cost_krw)
    values (v_sim.id, p_ticker, v_qty, v_px.price, v_unit_krw)
    on conflict (simulation_id, ticker) do update set
      avg_price    = (h.quantity * h.avg_price    + excluded.quantity * excluded.avg_price)    / (h.quantity + excluded.quantity),
      avg_cost_krw = (h.quantity * h.avg_cost_krw + excluded.quantity * excluded.avg_cost_krw) / (h.quantity + excluded.quantity),
      quantity     = h.quantity + excluded.quantity;
    update public.simulations set cash = cash - v_amount where id = v_sim.id;
  else
    select * into v_hold from public.holdings
     where simulation_id = v_sim.id and ticker = p_ticker for update;
    if not found or v_hold.quantity < v_qty then raise exception '보유 수량이 부족합니다'; end if;
    v_realized := round(v_qty * (v_unit_krw - v_hold.avg_cost_krw));
    if v_hold.quantity - v_qty < 0.00005 then
      delete from public.holdings where simulation_id = v_sim.id and ticker = p_ticker;
    else
      update public.holdings set quantity = quantity - v_qty
       where simulation_id = v_sim.id and ticker = p_ticker;
    end if;
    update public.simulations set cash = cash + v_amount where id = v_sim.id;
  end if;

  insert into public.trades (simulation_id, user_id, ticker, side, quantity, price, currency, fx_rate,
                             amount_krw, realized_pnl_krw, price_date, price_updated_at)
  values (v_sim.id, v_uid, p_ticker, v_side, v_qty, v_px.price, v_stock.currency, v_fx,
          v_amount, v_realized, v_px.price_date, v_px.updated_at);

  return json_build_object('ok', true, 'side', v_side, 'ticker', p_ticker, 'quantity', v_qty,
                           'price', v_px.price, 'fx_rate', v_fx, 'amount_krw', v_amount,
                           'realized_pnl_krw', v_realized);
end $$;

-- 액면분할/병합 반영 (수집 스크립트 전용). 분할 전 가격으로 산 주식 수만 비율대로 늘린다.
create or replace function public.apply_split(p_ticker text, p_date date, p_ratio numeric)
returns int
language plpgsql security definer
set search_path = public
as $$
declare
  v_h     record;
  v_pre   numeric;
  v_new   numeric;
  v_count int := 0;
begin
  if p_ratio is null or p_ratio <= 0 then raise exception 'bad ratio'; end if;
  if exists (select 1 from public.corporate_actions
             where ticker = p_ticker and action_date = p_date and kind = 'SPLIT' and applied) then
    return 0;  -- 이미 반영됨
  end if;

  for v_h in select * from public.holdings where ticker = p_ticker for update loop
    select coalesce(sum(case when side = 'BUY' then quantity else -quantity end), 0) into v_pre
      from public.trades
     where simulation_id = v_h.simulation_id and ticker = p_ticker and price_date < p_date;
    v_pre := greatest(0, least(v_pre, v_h.quantity));
    if v_pre > 0 then
      v_new := round(v_h.quantity + v_pre * (p_ratio - 1), 6);
      if v_new > 0 then
        update public.holdings
           set avg_price    = avg_price    * quantity / v_new,
               avg_cost_krw = avg_cost_krw * quantity / v_new,
               quantity     = v_new
         where simulation_id = v_h.simulation_id and ticker = p_ticker;
        v_count := v_count + 1;
      end if;
    end if;
  end loop;

  update public.prices_hourly set price = price / p_ratio
   where ticker = p_ticker and price_date < p_date;

  insert into public.corporate_actions (ticker, action_date, kind, ratio, applied, note)
  values (p_ticker, p_date, 'SPLIT', p_ratio, true, 'auto-applied to ' || v_count || ' holdings')
  on conflict (ticker, action_date, kind)
  do update set applied = true, ratio = excluded.ratio, note = excluded.note;

  return v_count;
end $$;

-- CSV에서 빠진 종목을 비활성화 (수집 스크립트 전용)
create or replace function public.deactivate_missing(p_tickers text[])
returns int
language plpgsql security definer
set search_path = public
as $$
declare v_n int;
begin
  if p_tickers is null or array_length(p_tickers, 1) is null then return 0; end if;
  update public.stocks set active = false, updated_at = now()
   where active and not (ticker = any (p_tickers));
  get diagnostics v_n = row_count;
  return v_n;
end $$;

-- 함수 실행 권한: 기본 공개 권한을 걷어내고 필요한 역할에만 부여
revoke all on function public.kst_today()                          from public, anon;
revoke all on function public.create_simulation(date, date)        from public, anon;
revoke all on function public.archive_simulation()                 from public, anon;
revoke all on function public.execute_trade(text, text, numeric)   from public, anon;
revoke all on function public.apply_split(text, date, numeric)     from public, anon, authenticated;
revoke all on function public.deactivate_missing(text[])           from public, anon, authenticated;

grant execute on function public.kst_today()                        to authenticated, service_role;
grant execute on function public.create_simulation(date, date)      to authenticated;
grant execute on function public.archive_simulation()               to authenticated;
grant execute on function public.execute_trade(text, text, numeric) to authenticated;
grant execute on function public.apply_split(text, date, numeric)   to service_role;
grant execute on function public.deactivate_missing(text[])         to service_role;

-- 끝. "Success. No rows returned" 가 보이면 정상입니다.
