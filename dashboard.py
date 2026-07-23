"""
투자 포트폴리오 대시보드 (경량·안정)
- 맨 위: 전체 총평가금 + 수익률 + 포트폴리오 생성
- 계좌별: 총평가금 + 수익률 (해외는 매수환율→현재환율 + 달러/원화 토글)
- 종목: 종목/수량 · 평가금/수익률 · 목표/현재비중 · 신호(매수·매도)/금액
설계: 백그라운드 스레드 없음, 캐시 개수 제한 → 메모리 안정
"""
import streamlit as st
import requests
import json
import os
import math

st.set_page_config(page_title="내 포트폴리오", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
div[data-testid="stButton"] > button {
    border-radius: 8px; border: 1px solid #2a2a2a;
    background: linear-gradient(180deg, #1c1c1c, #151515);
    color: #e0e0e0; padding: 5px 14px; font-weight: 700; font-size: 13px;
    width: auto; min-height: 0; white-space: nowrap;
}
div[data-testid="stButton"] > button:hover { border-color: #4dd2ff; color: #fff; }
div[data-testid="stCheckbox"] label p, div[data-testid="stToggle"] label p {
    font-size: 13px !important; font-weight: 700 !important;
}
div[data-testid="column"] { min-width: 0 !important; }
</style>
""", unsafe_allow_html=True)

FINNHUB_SYMBOLS = {"QQQ": "QQQ", "VOO": "VOO", "SOXX": "SOXX", "SPY": "SPY"}
PORTFOLIO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolios_data.json")


def _secret(key):
    try:
        return st.secrets.get(key)
    except Exception:
        return None


# ===== 구글 시트 저장 =====
@st.cache_resource
def _get_gsheet():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        sa = st.secrets.get("gcp_service_account")
        sid = st.secrets.get("gsheet_id")
        if not sa or not sid:
            return None
        creds = Credentials.from_service_account_info(
            dict(sa), scopes=["https://www.googleapis.com/auth/spreadsheets"])
        return gspread.authorize(creds).open_by_key(sid)
    except Exception:
        return None


def _gsheet_read(tab):
    try:
        sh = _get_gsheet()
        if not sh:
            return None
        try:
            ws = sh.worksheet(tab)
        except Exception:
            return None
        raw = ws.acell("A1").value
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _gsheet_write(tab, data):
    try:
        sh = _get_gsheet()
        if not sh:
            return False
        try:
            ws = sh.worksheet(tab)
        except Exception:
            ws = sh.add_worksheet(title=tab, rows=1, cols=1)
        ws.update_acell("A1", json.dumps(data, ensure_ascii=False))
        return True
    except Exception:
        return False


def load_portfolios():
    gs = _gsheet_read("portfolios")
    if gs is not None:
        return gs
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_portfolios():
    data = st.session_state.portfolios
    _gsheet_write("portfolios", data)
    try:
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ===== 시세 조회 =====
def is_korean(ticker):
    t = ticker.strip().upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        return True
    # 순수 6자리 숫자 = 국내 종목코드 (예: 000660, 233740)
    code = t.split(".")[0]
    if code.isdigit() and len(code) == 6:
        return True
    # 한글이 포함되면 국내 종목명
    if any('\uac00' <= ch <= '\ud7a3' for ch in ticker):
        return True
    return False


# ===== 한국투자증권 KIS API =====
KIS_BASE = "https://openapi.koreainvestment.com:9443"


@st.cache_data(ttl=3600 * 12, max_entries=2)
def _kis_token():
    """KIS 접근토큰 발급 (12시간 캐시). 실패 시 None."""
    ak = _secret("KIS_APP_KEY")
    sk = _secret("KIS_APP_SECRET")
    if not ak or not sk:
        return None
    try:
        r = requests.post(f"{KIS_BASE}/oauth2/tokenP",
                          json={"grant_type": "client_credentials", "appkey": ak, "appsecret": sk},
                          timeout=6)
        if r.status_code == 200:
            return r.json().get("access_token")
    except Exception:
        pass
    return None


@st.cache_data(ttl=60, max_entries=80)
def get_kis_quote(ticker):
    """국내 시세 (현재가 + 52주최고가) - 한투 API 1회 호출."""
    ak = _secret("KIS_APP_KEY")
    sk = _secret("KIS_APP_SECRET")
    token = _kis_token()
    if not token or not ak or not sk:
        return None
    code = ticker.split(".")[0].strip()
    if not (code.isdigit() and len(code) == 6):
        return None  # 6자리 숫자코드가 아니면 한투 조회 불가
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={
                "authorization": f"Bearer {token}",
                "appkey": ak, "appsecret": sk,
                "tr_id": "FHKST01010100",
                "custtype": "P",
            },
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            timeout=6)
        if r.status_code == 200:
            out = (r.json().get("output") or {})
            def _num(v):
                try:
                    return float(str(v).replace(",", "")) if v else None
                except Exception:
                    return None
            price = _num(out.get("stck_prpr"))
            if price:
                return {"price": price,
                        "high52": _num(out.get("w52_hgpr")),   # 52주 최고가
                        "low52": _num(out.get("w52_lwpr"))}
    except Exception:
        pass
    return None


def get_kis_price(ticker):
    q = get_kis_quote(ticker)
    return q["price"] if q else None


CRYPTO_ALIASES = {
    "BTC": "BTCUSDT", "비트코인": "BTCUSDT", "BITCOIN": "BTCUSDT", "BTCUSD": "BTCUSDT",
    "BTC-USD": "BTCUSDT", "BTCUSDT": "BTCUSDT", "ETH": "ETHUSDT", "이더리움": "ETHUSDT",
    "ETHUSD": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT", "DOGE": "DOGEUSDT",
}


# 한투 해외거래소 코드 (미국 종목은 어느 거래소인지 몰라서 순서대로 시도)
_KIS_OVERSEAS_EXCH = ["NAS", "NYS", "AMS"]  # 나스닥, 뉴욕, 아멕스
_KIS_EXCH_CACHE = {}  # 티커별 성공 거래소 기억


@st.cache_data(ttl=60, max_entries=80)
def get_kis_overseas_price(ticker):
    """미국 주식/ETF 현재가 (한국투자증권 해외주식 API)."""
    ak = _secret("KIS_APP_KEY")
    sk = _secret("KIS_APP_SECRET")
    token = _kis_token()
    if not token or not ak or not sk:
        return None
    sym = ticker.upper()
    # 이전에 성공한 거래소부터 시도
    known = _KIS_EXCH_CACHE.get(sym)
    exchanges = ([known] + [e for e in _KIS_OVERSEAS_EXCH if e != known]) if known else _KIS_OVERSEAS_EXCH
    for exch in exchanges:
        try:
            r = requests.get(
                f"{KIS_BASE}/uapi/overseas-price/v1/quotations/price",
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey": ak, "appsecret": sk,
                    "tr_id": "HHDFS00000300",
                    "custtype": "P",
                },
                params={"AUTH": "", "EXCD": exch, "SYMB": sym},
                timeout=6)
            if r.status_code == 200:
                d = r.json()
                out = d.get("output") or {}
                price = out.get("last") or out.get("stck_prpr")
                if price and float(str(price).replace(",", "")) > 0:
                    _KIS_EXCH_CACHE[sym] = exch  # 다음부터 이 거래소 우선
                    return float(str(price).replace(",", ""))
        except Exception:
            pass
    return None


@st.cache_data(ttl=3600, max_entries=60)
def get_finnhub_52w_high(ticker):
    """미국 종목 52주 최고가 (Finnhub metric - 무료 티어 지원)."""
    key = _secret("FINNHUB_API_KEY")
    if not key:
        return None
    try:
        r = requests.get("https://finnhub.io/api/v1/stock/metric",
                         params={"symbol": ticker.upper(), "metric": "all", "token": key},
                         timeout=6)
        if r.status_code == 200:
            m = r.json().get("metric") or {}
            v = m.get("52WeekHigh")
            if v:
                return float(v)
    except Exception:
        pass
    return None


def get_52w_high(ticker):
    """52주 최고가 - 국내는 한투(추가호출 없음), 미국은 Finnhub."""
    if is_korean(ticker):
        q = get_kis_quote(ticker)
        return q.get("high52") if q else None
    if is_crypto(ticker):
        return None
    return get_finnhub_52w_high(ticker)


def crypto_symbol(ticker):
    t = ticker.upper().replace("BINANCE:", "").strip()
    if t in CRYPTO_ALIASES:
        return CRYPTO_ALIASES[t]
    if t.endswith("USDT"):
        return t
    return None


def is_crypto(ticker):
    return crypto_symbol(ticker) is not None


_KR_SOURCE_ORDER = ["polling", "mobile", "daum", "crawl"]
_KR_GOOD_SOURCE = [None]  # 세션 중 성공한 소스 기억 (모듈 전역)


def _kr_fetch_one(source, code, hdr):
    """단일 소스에서 국내 현재가. 실패 시 None."""
    try:
        if source == "polling":
            r = requests.get(f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}",
                             headers={**hdr, "Referer": "https://finance.naver.com/"}, timeout=3.5)
            if r.status_code == 200:
                datas = r.json().get("datas") or []
                if datas and datas[0].get("closePrice"):
                    return float(str(datas[0]["closePrice"]).replace(",", ""))
        elif source == "mobile":
            r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/integration",
                             headers={**hdr, "Referer": "https://m.stock.naver.com/"}, timeout=3.5)
            if r.status_code == 200:
                d = r.json()
                cp = None
                if isinstance(d.get("dealTrendInfos"), list) and d["dealTrendInfos"]:
                    cp = d["dealTrendInfos"][0].get("closePrice")
                if not cp:
                    for it in (d.get("totalInfos") or []):
                        if it.get("code") in ("closePrice", "close"):
                            cp = it.get("value")
                            break
                if cp:
                    return float(str(cp).replace(",", ""))
        elif source == "daum":
            r = requests.get(f"https://finance.daum.net/api/quotes/A{code}",
                             headers={**hdr, "Referer": f"https://finance.daum.net/quotes/A{code}"}, timeout=3.5)
            if r.status_code == 200:
                tp = r.json().get("tradePrice")
                if tp:
                    return float(tp)
        elif source == "crawl":
            from bs4 import BeautifulSoup
            r = requests.get(f"https://finance.naver.com/item/main.naver?code={code}",
                             headers=hdr, timeout=3.5)
            soup = BeautifulSoup(r.text, "html.parser")
            tag = soup.select_one("p.no_today span.blind")
            if tag:
                return float(tag.text.replace(",", ""))
    except Exception:
        pass
    return None


@st.cache_data(ttl=90, max_entries=60)
def get_naver_price(ticker):
    """국내 현재가 - 세션 중 되는 소스를 우선 시도 (빠름)."""
    code = ticker.split(".")[0]
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
    good = _KR_GOOD_SOURCE[0]
    order = ([good] + [s for s in _KR_SOURCE_ORDER if s != good]) if good else _KR_SOURCE_ORDER
    for source in order:
        price = _kr_fetch_one(source, code, hdr)
        if price:
            _KR_GOOD_SOURCE[0] = source  # 다음부터 이 소스 우선
            return price
    return None


@st.cache_data(ttl=60, max_entries=60)
def get_finnhub_price(ticker):
    """미국 주식/ETF 현재가 (Finnhub)."""
    api_key = _secret("FINNHUB_API_KEY")
    if not api_key:
        return None
    sym = FINNHUB_SYMBOLS.get(ticker, ticker)
    try:
        res = requests.get("https://finnhub.io/api/v1/quote",
                           params={"symbol": sym, "token": api_key}, timeout=3.5)
        d = res.json()
        if d.get("c") and d["c"] > 0:
            return float(d["c"])
    except Exception:
        pass
    return None


@st.cache_data(ttl=60, max_entries=30)
def get_crypto_price(ticker):
    """암호화폐 현재가 (Binance → Coinbase 폴백)."""
    sym = crypto_symbol(ticker)
    if not sym:
        return None
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/price",
                           params={"symbol": sym}, timeout=3.5)
        d = res.json()
        if d.get("price"):
            return float(d["price"])
    except Exception:
        pass
    try:
        base = sym.replace("USDT", "")
        res = requests.get(f"https://api.coinbase.com/v2/prices/{base}-USD/spot", timeout=3.5)
        amt = res.json().get("data", {}).get("amount")
        if amt:
            return float(amt)
    except Exception:
        pass
    return None


def get_current_price(ticker):
    if is_korean(ticker):
        p = get_kis_price(ticker)   # 한투 국내 API 우선
        if p:
            return p
        return get_naver_price(ticker)  # 폴백: 네이버/다음
    if is_crypto(ticker):
        return get_crypto_price(ticker)
    # 미국 주식: 한투 해외 API 우선, 실패 시 Finnhub 폴백
    p = get_kis_overseas_price(ticker)
    if p:
        return p
    return get_finnhub_price(ticker)


@st.cache_data(ttl=120, max_entries=10)
def get_usd_krw():
    """원/달러 환율 - 여러 소스 폴백."""
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
    # 1) 네이버 모바일 마켓인덱스
    try:
        r = requests.get("https://m.stock.naver.com/front-api/marketIndex/productDetail",
                         params={"category": "exchange", "reutersCode": "FX_USDKRW"},
                         headers={**hdr, "Referer": "https://m.stock.naver.com/"}, timeout=3.5)
        if r.status_code == 200:
            d = r.json()
            v = (d.get("result") or {}).get("calcPrice") or (d.get("result") or {}).get("closePrice")
            if v:
                val = float(str(v).replace(",", ""))
                if 800 < val < 2500:
                    return val
    except Exception:
        pass
    # 2) 다음 환율
    try:
        r = requests.get("https://finance.daum.net/api/exchanges/FRX.KRWUSD",
                         headers={**hdr, "Referer": "https://finance.daum.net/exchanges"}, timeout=3.5)
        if r.status_code == 200:
            v = r.json().get("basePrice")
            if v and 800 < float(v) < 2500:
                return float(v)
    except Exception:
        pass
    # 3) 오픈 환율 API
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=3.5)
        if r.status_code == 200:
            v = r.json().get("rates", {}).get("KRW")
            if v and 800 < float(v) < 2500:
                return float(v)
    except Exception:
        pass
    # 4) 네이버 구 API
    try:
        r = requests.get("https://api.stock.naver.com/marketindex/exchange/FX_USDKRW",
                         headers={**hdr, "Referer": "https://m.stock.naver.com/"}, timeout=3.5)
        d = r.json()
        for k in ("closePrice", "calcPrice"):
            v = d.get(k)
            if v:
                val = float(str(v).replace(",", ""))
                if 800 < val < 2500:
                    return val
    except Exception:
        pass
    return 1380.0


# ===== 시장 지표 (공포지수·VIX·미국채) =====
@st.cache_data(ttl=1800, max_entries=3)
def get_fear_greed():
    """CNN 공포탐욕지수 (0~100). 실패 시 None."""
    try:
        r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code == 200:
            v = r.json().get("fear_and_greed", {}).get("score")
            if v is not None:
                return round(float(v))
    except Exception:
        pass
    return None


@st.cache_data(ttl=600, max_entries=3)
def get_vix():
    """VIX 변동성지수. Finnhub → 실패시 None."""
    api_key = _secret("FINNHUB_API_KEY")
    if api_key:
        for sym in ("^VIX", "VIX"):
            try:
                r = requests.get("https://finnhub.io/api/v1/quote",
                                 params={"symbol": sym, "token": api_key}, timeout=5)
                d = r.json()
                if d.get("c") and d["c"] > 0:
                    return round(float(d["c"]), 1)
            except Exception:
                pass
    return None


@st.cache_data(ttl=3600, max_entries=3)
def get_treasury_spread():
    """미국채 10년-2년 스프레드 (%p). FRED. 실패시 None."""
    api_key = _secret("FRED_API_KEY")
    if not api_key:
        return None
    try:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": "T10Y2Y", "api_key": api_key,
                                 "file_type": "json", "sort_order": "desc", "limit": 1},
                         timeout=5)
        if r.status_code == 200:
            obs = r.json().get("observations", [])
            if obs and obs[0].get("value") not in (".", None):
                return round(float(obs[0]["value"]), 2)
    except Exception:
        pass
    return None





# ===== 계산 =====
def fmt_won(v):
    return f"{v:,.0f}원"


def fmt_usd(v):
    return f"{v:,.2f}$"


def compute_account(holdings, cur_fx):
    """계좌 계산 (해외+국내 혼합, 환율 환산 포함)."""
    rows = []
    usd_buy_krw = usd_eval_krw = usd_fx_gain = 0.0
    krw_buy = krw_eval = 0.0
    usd_buy = usd_eval = 0.0
    has_usd = False
    for h in holdings:
        tk = h["ticker"]
        price = get_current_price(tk)
        buy_amt = h["qty"] * h["avg_price"]
        eval_amt = h["qty"] * price if price else buy_amt
        usd = not is_korean(tk)
        buy_fx = h.get("buy_fx_rate", 0) or cur_fx
        if usd:
            has_usd = True
            usd_buy += buy_amt
            usd_eval += eval_amt
            usd_buy_krw += buy_amt * buy_fx
            usd_eval_krw += eval_amt * cur_fx
            usd_fx_gain += buy_amt * (cur_fx - buy_fx)
        else:
            krw_buy += buy_amt
            krw_eval += eval_amt
        try:
            h52 = get_52w_high(tk)
        except Exception:
            h52 = None
        rows.append({**h, "price": price, "buy_amt": buy_amt, "eval_amt": eval_amt,
                     "usd": usd, "buy_fx": buy_fx, "high52": h52})
    return {
        "rows": rows,
        "total_buy_krw": usd_buy_krw + krw_buy,
        "total_eval_krw": usd_eval_krw + krw_eval,
        "fx_gain": usd_fx_gain,
        "has_usd": has_usd,
        "usd_buy": usd_buy, "usd_eval": usd_eval,
        "usd_buy_krw": usd_buy_krw,
    }


# ===== 다이얼로그 =====
@st.dialog("새 포트폴리오")
def create_account_dialog():
    name = st.text_input("계좌 이름", placeholder="예: 1. 연금")
    if st.button("만들기", use_container_width=True):
        if name and name not in st.session_state.portfolios:
            st.session_state.portfolios[name] = []
            save_portfolios()
            st.rerun()


@st.dialog("종목 추가")
def add_stock_dialog(acct):
    st.caption("미국: AAPL, QLD / 국내: 005930.KS / 코인: BTC")
    ticker = st.text_input("티커").strip().upper()
    name = st.text_input("종목명 (표시용)")
    qty = st.number_input("수량", min_value=0.0, step=1.0)
    avg = st.number_input("평단가", min_value=0.0, step=0.0001, format="%.4f")
    target = st.number_input("목표 비중 (%)", min_value=0.0, max_value=100.0, step=1.0)
    fx = 0.0
    if ticker and not is_korean(ticker):
        fx = st.number_input("매수 환율 (원/달러, 모르면 0)", min_value=0.0, step=1.0)
    if st.button("추가", use_container_width=True):
        if ticker:
            st.session_state.portfolios[acct].append({
                "ticker": ticker, "name": name or ticker, "qty": qty,
                "avg_price": avg, "target_weight": target, "buy_fx_rate": fx})
            save_portfolios()
            st.rerun()


@st.dialog("추가 매수")
def add_more_dialog(acct, idx):
    h = st.session_state.portfolios[acct][idx]
    st.markdown(f"**{h['name']}** · 기존 {h['qty']:,.0f}주 @ {h['avg_price']:.4f}")
    aq = st.number_input("추가 수량", min_value=0.0, step=1.0)
    ap = st.number_input("매수 단가", min_value=0.0, step=0.0001, format="%.4f")
    af = 0.0
    if not is_korean(h["ticker"]):
        af = st.number_input("매수 환율", min_value=0.0, step=1.0)
    if aq > 0 and ap > 0:
        nq = h["qty"] + aq
        na = (h["qty"] * h["avg_price"] + aq * ap) / nq
        of = h.get("buy_fx_rate", 0) or 0
        nf = ((h["qty"] * of + aq * af) / nq) if (not is_korean(h["ticker"]) and af > 0 and of) else (af or of)
        st.info(f"→ {nq:,.0f}주 @ {na:.4f}" + (f" (환율 {nf:.0f})" if nf else ""))
        if st.button("확정", use_container_width=True):
            h.update({"qty": nq, "avg_price": na, "buy_fx_rate": nf})
            save_portfolios()
            st.rerun()


@st.dialog("종목 수정 / 삭제")
def edit_stock_dialog(acct, idx):
    h = st.session_state.portfolios[acct][idx]
    name = st.text_input("종목명", value=h["name"])
    qty = st.number_input("수량", min_value=0.0, step=1.0, value=float(h["qty"]))
    avg = st.number_input("평단가", min_value=0.0, step=0.0001, format="%.4f", value=float(h["avg_price"]))
    target = st.number_input("목표 비중 (%)", min_value=0.0, max_value=100.0, step=1.0,
                             value=float(h.get("target_weight", 0)))
    fx = float(h.get("buy_fx_rate", 0) or 0)
    if not is_korean(h["ticker"]):
        fx = st.number_input("매수 환율", min_value=0.0, step=1.0, value=fx)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("저장", use_container_width=True):
            h.update({"name": name, "qty": qty, "avg_price": avg,
                      "target_weight": target, "buy_fx_rate": fx})
            save_portfolios()
            st.rerun()
    with c2:
        if st.button("🗑 삭제", use_container_width=True):
            st.session_state.portfolios[acct].pop(idx)
            save_portfolios()
            st.rerun()


@st.dialog("계좌 관리")
def manage_dialog(acct):
    holdings = st.session_state.portfolios[acct]
    st.markdown("**계좌 이름**")
    rc = st.columns([3, 1])
    with rc[0]:
        new_name = st.text_input("이름", value=acct, key=f"rn_{acct}", label_visibility="collapsed")
    with rc[1]:
        if st.button("변경", key=f"rnb_{acct}", use_container_width=True):
            if new_name and new_name != acct and new_name not in st.session_state.portfolios:
                st.session_state.portfolios = {
                    (new_name if k == acct else k): v for k, v in st.session_state.portfolios.items()}
                save_portfolios()
                st.rerun()
    st.markdown("<hr style='border-color:#222;margin:8px 0;'>", unsafe_allow_html=True)
    st.markdown(f"**종목 ({len(holdings)}개)**")
    if st.button("＋ 새 종목 추가", use_container_width=True):
        st.session_state["_open_add"] = acct
        st.rerun()
    for i, h in enumerate(holdings):
        st.markdown(f'{h["name"]} <span style="color:#888;font-size:12px;">{h["ticker"]} · {h["qty"]:,.0f}주</span>',
                    unsafe_allow_html=True)
        bc = st.columns(2)
        with bc[0]:
            if st.button("추가매수", key=f"mm_{acct}_{i}", use_container_width=True):
                st.session_state["_open_more"] = (acct, i)
                st.rerun()
        with bc[1]:
            if st.button("수정·삭제", key=f"me_{acct}_{i}", use_container_width=True):
                st.session_state["_open_edit"] = (acct, i)
                st.rerun()
    st.markdown("<hr style='border-color:#222;margin:8px 0;'>", unsafe_allow_html=True)
    if st.button("🗑 이 계좌 삭제", use_container_width=True):
        del st.session_state.portfolios[acct]
        save_portfolios()
        st.rerun()


# ===== 종목 카드 =====
def render_holdings(acct, data, cur_fx, show_krw):
    rows = data["rows"]
    total_eval = sum(r["eval_amt"] for r in rows) or 1

    st.markdown(
        '<div style="display:grid;grid-template-columns:1.05fr 1.75fr 0.65fr 0.75fr;gap:2px 0;'
        'padding:4px 6px;font-size:10px;color:#777;border-bottom:1px solid #222;margin-bottom:4px;">'
        '<div>종목 / 수량</div>'
        '<div style="text-align:right;">평가금 / 수익금(%)</div>'
        '<div style="text-align:center;">목표 / 현재</div>'
        '<div style="text-align:right;">신호 / 금액</div>'
        '</div>', unsafe_allow_html=True)

    for i, r in enumerate(rows):
        usd = r["usd"]
        profit = r["eval_amt"] - r["buy_amt"]
        profit_pct = (profit / r["buy_amt"] * 100) if r["buy_amt"] else 0
        pc = "#ff4d4d" if profit >= 0 else "#4d94ff"
        pa = "▲" if profit >= 0 else "▼"
        cur_w = r["eval_amt"] / total_eval * 100
        tgt_w = r.get("target_weight", 0) or 0

        def money(v):
            if usd and not show_krw:
                return fmt_usd(v)
            return fmt_won(v * cur_fx if usd else v)

        cw_color = "#888" if tgt_w == 0 else ("#ff4d4d" if cur_w > tgt_w else "#4d94ff")

        # 신호: 목표비중 ±7% 상대 밴드 → 매수/매도 금액 표시
        if tgt_w == 0:
            sig_html = '<span style="color:#666;font-size:13px;">-</span>'
        else:
            tgt_amt = tgt_w / 100 * total_eval
            diff = abs(tgt_amt - r["eval_amt"])
            upper = tgt_w * 1.10
            lower = tgt_w * 0.90
            if cur_w < lower:
                sig_html = (f'<div style="font-size:14px;font-weight:800;color:#ff4d4d;">매수</div>'
                            f'<div style="font-size:12px;color:#ff4d4d;">{fmt_won(diff)}</div>')
            elif cur_w > upper:
                sig_html = (f'<div style="font-size:14px;font-weight:800;color:#4d94ff;">매도</div>'
                            f'<div style="font-size:12px;color:#4d94ff;">{fmt_won(diff)}</div>')
            else:
                sig_html = '<div style="font-size:13px;font-weight:700;color:#888;">적정</div>'

        name_size = 14 if len(r["name"]) <= 9 else 12 if len(r["name"]) <= 14 else 10

        # 52주 고점 대비 하락률
        h52 = r.get("high52")
        price_now = r.get("price")
        if h52 and price_now and h52 > 0:
            dd = (price_now - h52) / h52 * 100
            dd_color = "#4d94ff" if dd < 0 else "#888"
            dd_html = f'<div style="font-size:10px;color:{dd_color};margin-top:2px;white-space:nowrap;">고점대비 {dd:.1f}%</div>'
        else:
            dd_html = ""

        st.markdown(
            f'<div style="background:#141414;border:1px solid #262626;border-radius:8px;padding:11px 10px;margin-bottom:6px;">'
            f'<div style="display:grid;grid-template-columns:1.05fr 1.75fr 0.65fr 0.75fr;gap:0;align-items:center;">'
            f'<div style="padding:2px 8px 2px 2px;overflow:hidden;min-width:0;">'
            f'<div style="font-size:{name_size}px;font-weight:800;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{r["name"]}</div>'
            f'<div style="font-size:13px;font-weight:700;color:#fff;margin-top:4px;white-space:nowrap;">{r["qty"]:,.0f}주</div>'
            f'{dd_html}</div>'
            f'<div style="text-align:right;padding:2px 8px;min-width:0;overflow:hidden;border-left:1px solid #3a3a3a;">'
            f'<div style="font-size:15px;font-weight:800;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{money(r["eval_amt"])}</div>'
            f'<div style="font-size:11px;font-weight:700;color:{pc};margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{pa}{money(abs(profit))} ({pa}{abs(profit_pct):.1f}%)</div></div>'
            f'<div style="text-align:center;padding:2px 4px;overflow:hidden;border-left:1px solid #3a3a3a;">'
            f'<div style="font-size:13px;font-weight:800;color:#fff;">{tgt_w:.0f}%</div>'
            f'<div style="font-size:13px;font-weight:800;color:{cw_color};margin-top:4px;">{cur_w:.0f}%</div></div>'
            f'<div style="text-align:right;padding:2px 2px 2px 6px;overflow:hidden;border-left:1px solid #3a3a3a;">{sig_html}</div>'
            f'</div></div>',
            unsafe_allow_html=True)


def summary_block(eval_krw, buy_krw, big=True):
    profit = eval_krw - buy_krw
    ppct = (profit / buy_krw * 100) if buy_krw else 0
    pc = "#ff4d4d" if profit >= 0 else "#4d94ff"
    pa = "▲" if profit >= 0 else "▼"
    sz = 28 if big else 22
    return (f'<div style="font-size:{sz}px;font-weight:800;color:#fff;line-height:1.1;">{eval_krw:,.0f}원</div>'
            f'<div style="font-size:14px;font-weight:700;color:{pc};margin-top:3px;">{pa} {abs(profit):,.0f}원 ({pa}{abs(ppct):.1f}%)</div>')


# ===== 메인 =====
if "portfolios" not in st.session_state:
    st.session_state.portfolios = load_portfolios()

# 후속 다이얼로그
_pa = st.session_state.pop("_open_add", None)
if _pa:
    add_stock_dialog(_pa)
_pm = st.session_state.pop("_open_more", None)
if _pm:
    add_more_dialog(_pm[0], _pm[1])
_pe = st.session_state.pop("_open_edit", None)
if _pe:
    edit_stock_dialog(_pe[0], _pe[1])

_top = st.columns([2.2, 1, 1])
with _top[0]:
    _total_ph = st.empty()
with _top[1]:
    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    if st.button("＋ 생성", key="create_acct"):
        create_account_dialog()
with _top[2]:
    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    if st.button("📊 비중", key="toggle_donut"):
        st.session_state["_show_donut"] = not st.session_state.get("_show_donut", False)
        st.rerun()

if not st.session_state.portfolios:
    st.info("계좌가 없습니다. '＋ 생성'으로 만들어보세요.")
else:
    cur_fx = get_usd_krw()
    names = list(st.session_state.portfolios.keys())
    acct_data = {}
    grand_buy = grand_eval = 0.0
    for nm in names:
        d = compute_account(st.session_state.portfolios[nm], cur_fx)
        acct_data[nm] = d
        grand_buy += d["total_buy_krw"]
        grand_eval += d["total_eval_krw"]

    if grand_eval > 0:
        _total_ph.markdown('<div style="padding:8px 2px 0;">' + summary_block(grand_eval, grand_buy, big=True) + '</div>',
                           unsafe_allow_html=True)

    # 비중(도넛) 토글 - 버튼
    if st.session_state.get("_show_donut"):
        items = []
        for nm in names:
            for rr in acct_data[nm]["rows"]:
                if rr["eval_amt"]:
                    ev = rr["eval_amt"] * (cur_fx if rr["usd"] else 1)
                    items.append((rr["name"], ev))
        if items:
            _tot = sum(v for _, v in items) or 1
            palette = ["#4dd2ff", "#ff9f4d", "#4dff88", "#ff4d4d", "#c04dff", "#ffd633",
                       "#4d94ff", "#ff4dcb", "#9fe14d", "#4dffea", "#ff7a4d", "#4dffb0"]
            _sz, _ro, _ri = 150, 68, 42
            _c = _sz / 2
            _rm = (_ro + _ri) / 2
            segs = labs = ""
            ang = -90.0
            for i, (an, av) in enumerate(sorted(items, key=lambda x: -x[1])):
                col = palette[i % len(palette)]
                pct = av / _tot * 100
                sw = pct / 100 * 360
                a0, a1 = math.radians(ang), math.radians(ang + sw)
                x0o, y0o = _c + _ro*math.cos(a0), _c + _ro*math.sin(a0)
                x1o, y1o = _c + _ro*math.cos(a1), _c + _ro*math.sin(a1)
                x0i, y0i = _c + _ri*math.cos(a1), _c + _ri*math.sin(a1)
                x1i, y1i = _c + _ri*math.cos(a0), _c + _ri*math.sin(a0)
                lg = 1 if sw > 180 else 0
                segs += f'<path d="M {x0o:.1f} {y0o:.1f} A {_ro} {_ro} 0 {lg} 1 {x1o:.1f} {y1o:.1f} L {x0i:.1f} {y0i:.1f} A {_ri} {_ri} 0 {lg} 0 {x1i:.1f} {y1i:.1f} Z" fill="{col}"/>'
                if pct >= 6:
                    ma = math.radians(ang + sw/2)
                    lx, ly = _c + _rm*math.cos(ma), _c + _rm*math.sin(ma)
                    labs += f'<text x="{lx:.1f}" y="{ly+3:.1f}" text-anchor="middle" font-size="11" font-weight="800" fill="#0a0a0a">{pct:.0f}</text>'
                ang += sw
            legend = ""
            for i, (an, av) in enumerate(sorted(items, key=lambda x: -x[1])):
                col = palette[i % len(palette)]
                pct = av / _tot * 100
                legend += (f'<span style="display:inline-flex;align-items:center;gap:4px;margin:2px 10px 2px 0;">'
                           f'<span style="width:9px;height:9px;border-radius:2px;background:{col};"></span>'
                           f'<span style="font-size:11px;color:#ccc;white-space:nowrap;">{an} {pct:.0f}%</span></span>')
            st.markdown(
                f'<div style="text-align:center;margin:10px 0 6px;">'
                f'<svg width="{_sz}" height="{_sz}" viewBox="0 0 {_sz} {_sz}">{segs}{labs}</svg></div>'
                f'<div style="line-height:1.7;margin-bottom:8px;">{legend}</div>',
                unsafe_allow_html=True)

    st.markdown("<div style='font-size:13px;color:#888;margin:14px 0 6px;'>계좌 목록</div>", unsafe_allow_html=True)

    for nm in names:
        d = acct_data[nm]
        holdings = st.session_state.portfolios[nm]
        buy_krw, eval_krw = d["total_buy_krw"], d["total_eval_krw"]

        # 계좌명 + 관리(+)
        hc = st.columns([4, 1])
        with hc[0]:
            st.markdown(f'<div style="padding-top:4px;font-size:16px;font-weight:800;color:#fff;">{nm} '
                        f'<span style="font-size:11px;color:#888;">({len(holdings)})</span></div>',
                        unsafe_allow_html=True)
        with hc[1]:
            if st.button("＋", key=f"mng_{nm}", help="종목 관리"):
                manage_dialog(nm)

        # 통화 토글 (해외) - 기본 원화, 달러는 눌러야
        show_krw = True
        if d["has_usd"]:
            cm = st.radio("통화", ["₩ 원화", "$ 달러"], horizontal=True,
                          key=f"cur_{nm}", label_visibility="collapsed")
            show_krw = (cm == "₩ 원화")

        # 계좌 요약 (총평가금 + 수익률)
        if buy_krw > 0:
            fx_html = ""
            if d["has_usd"]:
                ub = d.get("usd_buy", 0)
                avg_fx = (d["usd_buy_krw"] / ub) if ub and d.get("usd_buy_krw") else cur_fx
                fx_pct = ((cur_fx - avg_fx) / avg_fx * 100) if avg_fx else 0
                fxc = "#ff4d4d" if fx_pct >= 0 else "#4d94ff"
                fxa = "▲" if fx_pct >= 0 else "▼"
                fx_html = (f'<div style="font-size:12px;color:#888;margin-top:5px;">'
                           f'매수환율 <b style="color:#ccc;">{avg_fx:,.0f}</b> → 현재 <b style="color:#ccc;">{cur_fx:,.0f}</b> '
                           f'<b style="color:{fxc};">({fxa}{abs(fx_pct):.2f}%)</b></div>')
            st.markdown('<div style="padding:2px 2px 4px;">' + summary_block(eval_krw, buy_krw, big=False)
                        + fx_html + '</div>', unsafe_allow_html=True)

        if holdings:
            render_holdings(nm, d, cur_fx, show_krw)

        st.markdown("<div style='height:8px;border-bottom:1px solid #2a2a2a;margin-bottom:12px;'></div>",
                    unsafe_allow_html=True)
