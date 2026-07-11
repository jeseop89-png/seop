import streamlit as st
import yfinance as yf
import time
import requests
from bs4 import BeautifulSoup
import re
import json
import os
import pandas as pd
import math
import concurrent.futures
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

# 1. 페이지 기본 설정 및 여백 최적화
st.set_page_config(page_title="글로벌 실시간 지수 및 금리 대시보드", layout="wide")
st.markdown("<style>.block-container {padding-top: 3.5rem; padding-bottom: 0rem;}</style>", unsafe_allow_html=True)
st.markdown(
    """
    <style>
    div[data-testid="stButton"] > button {
        border-radius: 10px;
        border: 1px solid #2a2a2a;
        background: linear-gradient(180deg, #1c1c1c, #151515);
        color: #e0e0e0;
        padding: 11px 18px;
        font-weight: 700;
        font-size: 14px;
        transition: all 0.18s ease;
        box-shadow: 0 1px 2px rgba(0,0,0,0.3);
    }
    div[data-testid="stButton"] > button:hover {
        border-color: #4dd2ff;
        color: #ffffff;
        background: linear-gradient(180deg, #1e2a30, #16222a);
        box-shadow: 0 2px 8px rgba(77,210,255,0.15);
        transform: translateY(-1px);
    }
    div[data-testid="stButton"] > button:active {
        border-color: #4dd2ff;
        color: #ffffff;
        transform: translateY(0px);
    }
    /* 합산 체크박스·상세보기 토글 라벨을 더 크고 잘 보이게 */
    div[data-testid="stCheckbox"] label p,
    div[data-testid="stToggle"] label p {
        font-size: 15px !important;
        font-weight: 700 !important;
    }
    div[data-testid="stCheckbox"] label,
    div[data-testid="stToggle"] label {
        padding: 6px 0 !important;
    }
    /* 모바일에서 st.columns가 한 줄로 쭉 세로로 쌓이지 않고, 화면 폭에 맞춰
       2개씩 자동으로 줄바꿈되는 격자(그리드)로 보이게 함 (옆으로 스크롤 불필요) */
    @media (max-width: 900px) {
        div[data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
            row-gap: 10px;
        }
        div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
            flex: 1 1 44% !important;
            min-width: 44% !important;
            width: auto !important;
        }
        div[data-testid="stButton"] > button {
            font-size: 15px;
            padding: 6px 12px;
        }
        /* 매크로 더보기 안의 지표 카드는 모바일에서도 3개씩 유지 */
        div[class*="st-key-macro_row"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
            flex: 1 1 30% !important;
            min-width: 30% !important;
        }
        div[class*="st-key-macro_row"] div[data-testid="stHorizontalBlock"] {
            gap: 6px !important;
        }
        /* 포트폴리오 "테이블 보기"는 위 그리드 규칙에서 예외 처리해서
           PC와 완전히 똑같은 모양을 유지한 채, 옆으로 스크롤해서 보게 함 */
        div[class*="st-key-pc_table_"] div[data-testid="stHorizontalBlock"] {
            flex-wrap: nowrap !important;
            overflow-x: auto !important;
            -webkit-overflow-scrolling: touch;
        }
        div[class*="st-key-pc_table_"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
            flex: 0 0 auto !important;
            min-width: 90px !important;
            width: auto !important;
        }
        /* 자동 모드: 좁은 화면(모바일)에서는 카드만 보이고 테이블은 숨김 */
        div[class*="st-key-auto_table_"] {
            display: none !important;
        }
    }
    @media (min-width: 901px) {
        /* 자동 모드: 넓은 화면(PC)에서는 테이블만 보이고 카드는 숨김 */
        div[class*="st-key-auto_card_"] {
            display: none !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True
)

# ==========================================
# 💾 포트폴리오 파일 저장/불러오기 (JSON)
#   -> 앱을 껐다 켜거나 페이지를 새로고침해도
#      portfolios_data.json 파일에서 데이터를 복원함
# ==========================================
PORTFOLIO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolios_data.json")
WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist_data.json")


# ==========================================
# 💾 구글 시트 저장소 (재배포/재시작해도 데이터 유지)
#   -> Streamlit Cloud는 로컬 파일이 재배포마다 초기화되므로,
#      구글 시트를 DB처럼 써서 포트폴리오를 영구 보존.
#      st.secrets에 gcp_service_account + gsheet_id 가 있으면 시트 사용,
#      없으면 기존처럼 로컬 파일 사용 (로컬 개발 환경 대비).
# ==========================================
@st.cache_resource
def _get_gsheet():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        sa_info = st.secrets.get("gcp_service_account")
        sheet_id = st.secrets.get("gsheet_id")
        if not sa_info or not sheet_id:
            return None
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(dict(sa_info), scopes=scopes)
        gc = gspread.authorize(creds)
        return gc.open_by_key(sheet_id)
    except Exception:
        return None


def _gsheet_read(tab_name):
    """구글 시트의 특정 탭 A1 셀에 통째로 저장된 JSON 문자열을 읽어 파싱."""
    try:
        sh = _get_gsheet()
        if not sh:
            return None
        try:
            ws = sh.worksheet(tab_name)
        except Exception:
            return None
        raw = ws.acell("A1").value
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _gsheet_write(tab_name, data):
    """데이터를 JSON 문자열로 직렬화해 구글 시트 탭 A1 셀에 저장."""
    try:
        sh = _get_gsheet()
        if not sh:
            return False
        try:
            ws = sh.worksheet(tab_name)
        except Exception:
            ws = sh.add_worksheet(title=tab_name, rows=1, cols=1)
        ws.update_acell("A1", json.dumps(data, ensure_ascii=False))
        return True
    except Exception:
        return False


def load_portfolios():
    # 1순위: 구글 시트
    gs = _gsheet_read("portfolios")
    if gs is not None:
        return gs
    # 2순위: 로컬 파일
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_portfolios():
    data = st.session_state.portfolios
    # 구글 시트에 저장 (성공하면 여기서 영구 보존됨)
    _gsheet_write("portfolios", data)
    # 로컬에도 백업 저장
    try:
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 과거에 중복 방지 로직이 없던 시절 저장된 중복 티커 제거
                seen = set()
                deduped = []
                for item in data:
                    t = item.get("ticker")
                    if t and t not in seen:
                        seen.add(t)
                        deduped.append(item)
                return deduped
        except Exception:
            return []
    return []


def save_watchlist():
    try:
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(st.session_state.watchlist, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"관심종목 저장 중 오류가 발생했습니다: {e}")


# ==========================================
# ⚙️ 현재가는 실시간 크롤링 + 52주 최고가는 yfinance 계산
#   -> 1년치 일봉 데이터(history)는 무거운 요청이라, 52주 최고가/전일종가처럼
#      자주 안 바뀌는 값은 따로 떼어내 몇 시간에 한 번만 다시 가져오도록 캐시.
#      덕분에 30~60초마다 도는 자동 갱신에서는 가벼운 현재가 조회만 반복됨.
# ==========================================
@st.cache_data(ttl=3600 * 4)
def get_year_history_stats(ticker):
    try:
        df = yf.Ticker(ticker).history(period="1y")
        if not df.empty and len(df) >= 2:
            return {
                "prev_close": float(df['Close'].iloc[-2]),
                "last_close": float(df['Close'].iloc[-1]),
                "high_52w": float(df['High'].max()),
            }
    except Exception:
        pass
    return None


@st.cache_data(ttl=120)
def get_korean_index_final(code):
    ticker_map = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"}
    ticker = ticker_map.get(code)

    current_price = None
    change_pct = 0.0
    high_52w = None

    try:
        url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=3)
        soup = BeautifulSoup(res.text, "html.parser")

        current_price = float(soup.find("em", {"id": "now_value"}).text.replace(",", ""))
        change_text = soup.find("span", {"id": "change_value_and_rate"}).text.strip()
        pct_match = re.search(r'([+-]?\d+\.\d+)%', change_text)
        change_pct = float(pct_match.group(1)) if pct_match else 0.0
    except:
        pass

    stats = get_year_history_stats(ticker)
    if stats:
        if current_price is None:
            current_price = stats["last_close"]
            change_pct = ((current_price - stats["prev_close"]) / stats["prev_close"]) * 100
        high_52w = stats["high_52w"]

    if high_52w is None or high_52w < 100:
        high_52w = 2892.21 if code == "KOSPI" else 923.15
    if current_price is None:
        current_price = 2650.0 if code == "KOSPI" else 845.0

    drop_pct = ((current_price - high_52w) / high_52w) * 100
    return {"current": current_price, "change_pct": change_pct, "high": high_52w, "drop": drop_pct}


# ==========================================
# 🌏 네이버 "월드증시" 페이지로 해외지수 가져오기 (실험적)
#   -> 한국 사이트라 클라우드 서버에서도 코스피처럼 빠를 가능성이 높음.
#      단, 심볼 코드/페이지 구조가 바뀌면 실패할 수 있어 실패 시
#      자동으로 기존 yfinance 방식으로 폴백함 (아래 get_index_data 참고).
# ==========================================
NAVER_WORLD_SYMBOLS = {
    "^GSPC": "SPI@SPX",    # S&P 500
    "^IXIC": "NAS@IXIC",   # 나스닥 종합
    "^N225": "NII@NI225",  # 니케이225
    # ^SOX(필라델피아 반도체지수)는 정확한 네이버 심볼을 확인 못해 제외 (yfinance 그대로)
}


@st.cache_data(ttl=120)
def get_naver_world_index(naver_symbol):
    try:
        url = f"https://finance.naver.com/world/sise.naver?symbol={naver_symbol}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=3)
        soup = BeautifulSoup(res.text, "html.parser")

        price_tag = soup.select_one("#now_value") or soup.select_one(".now_value")
        change_tag = soup.select_one("#change_value_and_rate") or soup.select_one(".change_value_and_rate")
        if not price_tag:
            return None

        current_price = float(price_tag.text.replace(",", "").strip())
        change_pct = 0.0
        if change_tag:
            m = re.search(r'([+-]?\d+\.\d+)%', change_tag.text)
            if m:
                change_pct = float(m.group(1))
        return {"current": current_price, "change_pct": change_pct}
    except Exception:
        return None


# Finnhub 티커 매핑: 야후 심볼 -> Finnhub 심볼 (대부분 동일, ETF/주식은 그대로)
FINNHUB_SYMBOLS = {
    "QQQ": "QQQ", "VOO": "VOO", "SOXX": "SOXX", "EWJ": "EWJ", "SPY": "SPY",
    "SHY": "SHY", "GC=F": "OANDA:XAU_USD", "CL=F": "WTICOUSD",
    "USDKRW=X": "OANDA:USD_KRW",
    # ^VIX, ^TNX 등 순수 지수는 Finnhub 무료로 안 되므로 매핑 안 함 (야후 폴백)
}


def get_yf_live_price(ticker):
    try:
        fi = yf.Ticker(ticker).fast_info
        for key in ("last_price", "lastPrice", "regular_market_price", "regularMarketPrice"):
            try:
                val = fi[key]
                if val:
                    return float(val)
            except Exception:
                continue
    except Exception:
        pass
    return None


@st.cache_data(ttl=60)
def get_finnhub_quote(ticker):
    """Finnhub 정식 API로 실시간 시세 조회. 야후(yfinance)와 달리 IP 차단이 없어
    클라우드에서도 빠르고 안정적. API 키는 Streamlit Secrets의 FINNHUB_API_KEY에 등록."""
    try:
        api_key = st.secrets.get("FINNHUB_API_KEY")
    except Exception:
        api_key = None
    if not api_key:
        return None

    fh_symbol = FINNHUB_SYMBOLS.get(ticker, ticker)
    try:
        url = "https://finnhub.io/api/v1/quote"
        params = {"symbol": fh_symbol, "token": api_key}
        res = requests.get(url, params=params, timeout=5)
        res.raise_for_status()
        d = res.json()
        # c=현재가, dp=전일대비 변동률(%), h/l=당일 고저, pc=전일종가
        if d.get("c") and d["c"] > 0:
            return {
                "current": d["c"],
                "change_pct": d.get("dp") if d.get("dp") is not None else 0.0,
            }
    except Exception:
        pass
    return None


@st.cache_data(ttl=120)
def get_index_data(ticker):
    stats = get_year_history_stats(ticker)  # 52주 최고가/전일종가 (실패하면 None일 수 있음)
    try:
        current_price = None
        change_pct = None

        # 1순위: Finnhub 정식 API (등록된 심볼만) - 실시간 + IP 차단 없음
        fh = get_finnhub_quote(ticker)
        if fh:
            current_price = fh["current"]
            change_pct = fh["change_pct"]

        # 2순위: 네이버 월드증시 (지원하는 지수만)
        if current_price is None:
            naver_symbol = NAVER_WORLD_SYMBOLS.get(ticker)
            if naver_symbol:
                naver_data = get_naver_world_index(naver_symbol)
                if naver_data:
                    current_price = naver_data["current"]
                    change_pct = naver_data["change_pct"]

        # 3순위: yfinance 폴백
        if current_price is None:
            current_price = get_yf_live_price(ticker)
            if current_price is None and stats:
                current_price = stats["last_close"]
            if current_price is None:
                return None
            if change_pct is None and stats:
                change_pct = ((current_price - stats["prev_close"]) / stats["prev_close"]) * 100

        if change_pct is None:
            change_pct = 0.0

        # 52주 최고가 데이터가 있으면 넣고, 없으면 생략
        if stats:
            high_52w = stats["high_52w"]
            drop_pct = ((current_price - high_52w) / high_52w) * 100
            return {"current": current_price, "change_pct": change_pct, "high": high_52w, "drop": drop_pct}
        else:
            return {"current": current_price, "change_pct": change_pct, "high": None, "drop": None}
    except Exception:
        return None


def _unused_old_get_index_data(ticker):
    stats = get_year_history_stats(ticker)  # 52주 최고가/전일종가 (실패하면 None일 수 있음)
    try:
        current_price = None
        change_pct = None

        # 네이버 월드증시로 먼저 시도 (지원하는 지수만) - 코스피처럼 빠를 가능성이 높음
        naver_symbol = NAVER_WORLD_SYMBOLS.get(ticker)
        if naver_symbol:
            naver_data = get_naver_world_index(naver_symbol)
            if naver_data:
                current_price = naver_data["current"]
                change_pct = naver_data["change_pct"]

        # 네이버가 실패했거나 지원 안 하는 지수는 기존 yfinance 방식으로 폴백
        if current_price is None:
            current_price = get_yf_live_price(ticker)
            if current_price is None and stats:
                current_price = stats["last_close"]
            if current_price is None:
                return None  # 현재가를 아예 못 구하면 표시 불가
            if change_pct is None and stats:
                change_pct = ((current_price - stats["prev_close"]) / stats["prev_close"]) * 100

        if change_pct is None:
            change_pct = 0.0

        # 52주 최고가 데이터가 있으면 넣고, 없으면(1년 히스토리 실패) 생략
        if stats:
            high_52w = stats["high_52w"]
            drop_pct = ((current_price - high_52w) / high_52w) * 100
            return {"current": current_price, "change_pct": change_pct, "high": high_52w, "drop": drop_pct}
        else:
            return {"current": current_price, "change_pct": change_pct, "high": None, "drop": None}
    except Exception:
        return None


@st.cache_data(ttl=3600 * 6)
def scrape_rate_from_tradingeconomics(country_path):
    """TradingEconomics 한국어 페이지에서 '~기준 금리는 마지막으로 X%로 기록되었습니다' 문구를 파싱.
    사이트 구조가 바뀌거나 차단되면 조용히 None을 반환 (호출부에서 수동값으로 폴백)."""
    try:
        url = f"https://ko.tradingeconomics.com/{country_path}/interest-rate"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        res = requests.get(url, headers=headers, timeout=5)
        text = BeautifulSoup(res.text, "html.parser").get_text(" ", strip=True)
        m = re.search(r'기준\s*금리는\s*(?:최근|마지막으로)\s*([\d]+(?:\.[\d]+)?)\s*(?:퍼센트|%)', text)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return None


@st.cache_data(ttl=3600 * 6)
def get_safe_rates_engine():
    # ⚠️ 한국(BOK)·일본(BOJ) 기준금리: 공식 무료 API가 없어 TradingEconomics 페이지
    #    크롤링으로 자동화를 시도합니다. 사이트 구조가 바뀌면 크롤링이 실패할 수 있고,
    #    이 경우 아래 MANUAL_FALLBACK 값으로 안전하게 되돌아갑니다.
    #    (크롤링이 계속 실패하면 이 값을 최신 금리로 직접 갱신해주세요)
    MANUAL_LAST_CONFIRMED = "2026-06-01"
    MANUAL_FALLBACK = {
        "USA": 3.75,
        "KOR": 2.50,
        "JPN": 1.00,
    }
    rates = {
        "USA": {"rate": MANUAL_FALLBACK["USA"], "status": "stay", "change": 0.00, "source": "manual"},
        "KOR": {"rate": MANUAL_FALLBACK["KOR"], "status": "stay", "change": 0.00, "source": "manual"},
        "JPN": {"rate": MANUAL_FALLBACK["JPN"], "status": "stay", "change": 0.00, "source": "manual"},
    }

    # 미국(FRED)·한국/일본(TradingEconomics 크롤링)을 동시에 가져와서 대기 시간을 줄임
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        us_future = pool.submit(fetch_fred_series, "DFEDTARU")
        kor_future = pool.submit(scrape_rate_from_tradingeconomics, "south-korea")
        jpn_future = pool.submit(scrape_rate_from_tradingeconomics, "japan")

        try:
            us_data = us_future.result()
            if us_data:
                latest_date, latest_rate = us_data[-1]
                rates["USA"]["rate"] = latest_rate
                rates["USA"]["source"] = "fred"
                if len(us_data) > 1:
                    prev_rate = us_data[-2][1]
                    if latest_rate > prev_rate:
                        rates["USA"]["status"] = "up"
                        rates["USA"]["change"] = round(latest_rate - prev_rate, 2)
                    elif latest_rate < prev_rate:
                        rates["USA"]["status"] = "down"
                        rates["USA"]["change"] = round(prev_rate - latest_rate, 2)
        except Exception:
            pass

        scrape_results = {"KOR": None, "JPN": None}
        try:
            scrape_results["KOR"] = kor_future.result()
        except Exception:
            pass
        try:
            scrape_results["JPN"] = jpn_future.result()
        except Exception:
            pass

    for key, scraped in scrape_results.items():
        if scraped is not None:
            prev_rate = MANUAL_FALLBACK[key]
            rates[key]["rate"] = scraped
            rates[key]["source"] = "scrape"
            if scraped > prev_rate:
                rates[key]["status"] = "up"
                rates[key]["change"] = round(scraped - prev_rate, 2)
            elif scraped < prev_rate:
                rates[key]["status"] = "down"
                rates[key]["change"] = round(prev_rate - scraped, 2)

    for key in rates:
        if rates[key]["source"] == "manual":
            rates[key]["last_confirmed"] = MANUAL_LAST_CONFIRMED

    return rates


@st.cache_data(ttl=120)
def get_cnn_fear_greed():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.cnn.com/"
    }
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        res = requests.get(url, headers=headers, timeout=3)
        if res.status_code == 200:
            json_data = res.json()
            score = int(json_data['fear_and_greed']['score'])
            rating = json_data['fear_and_greed']['rating'].lower()

            if "extreme fear" in rating: return score, "극단적 공포"
            elif "fear" in rating: return score, "공포"
            elif "neutral" in rating: return score, "중립"
            elif "extreme greed" in rating: return score, "극단적 탐욕"
            else: return score, "탐욕"
    except:
        pass

    try:
        vix_df = yf.Ticker("^VIX").history(period="5d")
        vix = vix_df['Close'].iloc[-1] if not vix_df.empty else 14.5
        vix_score = max(0, min(100, (28 - vix) * 6.25))

        spy_df = yf.Ticker("SPY").history(period="50d")
        if not spy_df.empty:
            spy_close = spy_df['Close'].iloc[-1]
            spy_ma = spy_df['Close'].mean()
            momentum_score = max(0, min(100, ((spy_close / spy_ma) - 0.96) * 1250))
        else:
            momentum_score = 50
        final_score = int((vix_score * 0.4) + (momentum_score * 0.6))
    except:
        final_score = 50

    if final_score <= 25: return final_score, "극단적 공포"
    elif final_score <= 45: return final_score, "공포"
    elif final_score <= 55: return final_score, "중립"
    elif final_score <= 75: return final_score, "탐욕"
    else: return final_score, "극단적 탐욕"


# ==========================================
# 📉 FRED 공개 데이터 (하이일드 스프레드 / 글로벌 M2)
#   -> API 키 없이 접근 가능한 fredgraph.csv 엔드포인트 사용
# ==========================================
@st.cache_data(ttl=3600 * 3)
def fetch_fred_series(series_id):
    # 1순위: FRED 공식 API (무료 키 필요, Streamlit Secrets에 FRED_API_KEY로 등록 시 사용)
    # 스크래핑용 CSV 엔드포인트보다 훨씬 안정적이고 클라우드 IP 차단 이슈가 없음
    try:
        api_key = st.secrets.get("FRED_API_KEY")
    except Exception:
        api_key = None

    if api_key:
        try:
            url = "https://api.stlouisfed.org/fred/series/observations"
            params = {"series_id": series_id, "api_key": api_key, "file_type": "json"}
            res = requests.get(url, params=params, timeout=6)
            res.raise_for_status()
            obs = res.json().get("observations", [])
            valid = [(o["date"], float(o["value"])) for o in obs if o.get("value") not in (None, ".", "")]
            if valid:
                return valid
        except Exception:
            pass  # 실패하면 아래 CSV 방식으로 폴백

    # 2순위: CSV 스크래핑 방식 (API 키가 없거나 API 호출이 실패했을 때)
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/csv,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    # 재시도는 지속적으로 실패할 경우 페이지 전체를 몇십 초씩 붙잡아두는 원인이 되므로,
    # 한 번만 시도하고 짧게 실패하도록 함 (막혀있는 경우 재시도해도 소용없음)
    try:
        res = requests.get(url, headers=headers, timeout=6)
        res.raise_for_status()
        lines = [l for l in res.text.strip().split("\n") if l.strip()]
        rows = [l.split(",") for l in lines[1:]]
        valid = [(d, float(v)) for d, v in rows if v not in (".", "")]
        return valid if valid else None
    except Exception:
        return None


@st.cache_data(ttl=3600 * 3)
def get_high_yield_spread():
    # ICE BofA US High Yield Index Option-Adjusted Spread (일간)
    data = fetch_fred_series("BAMLH0A0HYM2")
    if not data:
        return None
    date, value = data[-1]
    change = None
    if len(data) > 20:
        change = value - data[-21][1]  # 약 1개월(영업일 20일) 전 대비
    return {"value": value, "date": date, "change": change}


@st.cache_data(ttl=3600 * 6)
def get_global_m2():
    # 미국 M2(M2SL, 10억달러) + 유로존 M2(MYAGM2EZM196N, 유로) 합산
    # ※ 중국·일본 M2는 FRED 공개 시계열이 중단되어 제외 (2019년 이후 갱신 안됨)
    # 두 시리즈를 동시에 가져와서(순차 대기보다 두 배 빠름) 대기 시간을 줄임
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        us_future = pool.submit(fetch_fred_series, "M2SL")
        eu_future = pool.submit(fetch_fred_series, "MYAGM2EZM196N")
        us = us_future.result()
        eu = eu_future.result()
    if not us or not eu:
        return None

    fx = get_index_data("EURUSD=X")
    eur_usd_rate = fx["current"] if fx else 1.08

    us_map = {d[:7]: v for d, v in us}
    eu_map = {d[:7]: v for d, v in eu}
    common_months = sorted(set(us_map.keys()) & set(eu_map.keys()))

    if not common_months:
        return None

    # 최근 24개월 추세 (스파크라인용)
    recent_months = common_months[-24:]
    trend = [(us_map[m] / 1000.0) + ((eu_map[m] * eur_usd_rate) / 1e12) for m in recent_months]

    total_trillion = trend[-1]
    us_date = recent_months[-1] + "-01"

    yoy = None
    if len(common_months) > 12:
        base_month = common_months[-13]
        base_val = (us_map[base_month] / 1000.0) + ((eu_map[base_month] * eur_usd_rate) / 1e12)
        yoy = ((total_trillion - base_val) / base_val) * 100

    return {"total_trillion": total_trillion, "date": us_date, "yoy": yoy, "trend": trend}


def make_sparkline_svg(values, width=110, height=32, color="#4dff4d"):
    """작은 추세선(스파크라인) SVG 생성 - 값이 오르면 우상향, 내리면 우하향으로 직관적 표현.
    값들의 변화폭이 작아도 곡선 모양이 잘 보이도록 세로 공간을 최대한 활용."""
    if not values or len(values) < 2:
        return ""
    min_v, max_v = min(values), max(values)
    range_v = (max_v - min_v) if max_v != min_v else 1
    pad_x = 2
    pad_y = 4  # 위아래 여백을 줄여 곡선이 세로 공간을 최대한 쓰게
    step = (width - pad_x * 2) / (len(values) - 1)
    points = []
    for i, v in enumerate(values):
        x = pad_x + i * step
        y = pad_y + (height - pad_y * 2) * (1 - (v - min_v) / range_v)
        points.append((x, y))
    points_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_points = f"{pad_x:.1f},{height - pad_y:.1f} " + points_str + f" {width - pad_x:.1f},{height - pad_y:.1f}"
    last_x, last_y = points[-1]
    return f"""<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" preserveAspectRatio="none" style="display:block; max-width:100%;">
        <polygon points="{area_points}" fill="{color}" opacity="0.18"/>
        <polyline points="{points_str}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
        <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.5" fill="{color}" vector-effect="non-scaling-stroke"/>
    </svg>"""


def make_gauge_svg(score, width=140, height=76, r=58, label=""):
    """CNN 공포·탐욕지수 스타일의 반원 계기판. 컬러 밴드 없이 회색 반원 하나 +
    바늘 + 가운데 점수 숫자, 아래 상태 텍스트(예: Fear)로 실제 CNN처럼 표현."""
    score = max(0, min(100, score))
    cx, cy = width / 2, height - 4

    # 회색 반원 아크 하나 (180도 -> 0도)
    arc_pts = []
    steps = 40
    for i in range(steps + 1):
        t = 180 - (180 * i / steps)
        rad = math.radians(t)
        x = cx + r * math.cos(rad)
        y = cy - r * math.sin(rad)
        arc_pts.append(f"{x:.1f},{y:.1f}")
    arc_svg = f'<polyline points="{" ".join(arc_pts)}" fill="none" stroke="#555555" stroke-width="7" stroke-linecap="round" />'

    # 눈금 (0, 25, 50, 75, 100) + 숫자 라벨
    ticks_svg = ""
    for val in (0, 25, 50, 75, 100):
        t = 180 - (val / 100) * 180
        rad = math.radians(t)
        x_out = cx + (r + 4) * math.cos(rad)
        y_out = cy - (r + 4) * math.sin(rad)
        x_in = cx + (r - 5) * math.cos(rad)
        y_in = cy - (r - 5) * math.sin(rad)
        ticks_svg += f'<line x1="{x_in:.1f}" y1="{y_in:.1f}" x2="{x_out:.1f}" y2="{y_out:.1f}" stroke="#888888" stroke-width="1.5" />'
        x_lbl = cx + (r + 12) * math.cos(rad)
        y_lbl = cy - (r + 12) * math.sin(rad)
        ticks_svg += f'<text x="{x_lbl:.1f}" y="{y_lbl + 3:.1f}" text-anchor="middle" font-size="8" fill="#888888">{val}</text>'

    needle_theta = 180 - (score / 100) * 180
    rad = math.radians(needle_theta)
    needle_len = r - 10
    nx = cx + needle_len * math.cos(rad)
    ny = cy - needle_len * math.sin(rad)

    return f"""<svg width="100%" height="{height}" viewBox="-14 0 {width + 28} {height}" preserveAspectRatio="xMidYMax meet" style="display:block; max-width:100%;">
        {arc_svg}
        {ticks_svg}
        <line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" stroke="#ffffff" stroke-width="2.5" stroke-linecap="round"/>
        <circle cx="{cx}" cy="{cy}" r="4" fill="#ffffff"/>
        <text x="{cx}" y="{cy - 14}" text-anchor="middle" font-size="20" font-weight="800" fill="#ffffff">{score}</text>
    </svg>"""


# ==========================================
# 🔄 상단/중단/하단 시세 영역을 fragment로 분리
#   -> 10초마다 이 영역만 새로고침되고, 아래 포트폴리오는 영향 안받음
# ==========================================
@st.fragment(run_every=60)
# ==========================================
@st.cache_data(ttl=60)
def get_recent_closes(ticker):
    try:
        # Wilder 스무딩이 안정적으로 수렴하려면 넉넉한 기간이 필요
        df = yf.Ticker(ticker).history(period="6mo")
        if not df.empty:
            return df['Close'].tolist()
    except Exception:
        pass
    return []


def calculate_rsi(closes, period=14):
    # Wilder's Smoothing 방식 (HTS/MTS/TradingView 등 대부분의 플랫폼과 동일한 계산법)
    if len(closes) < period + 1:
        return None
    s = pd.Series(closes)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    value = rsi.iloc[-1]
    return None if pd.isna(value) else float(value)


def get_rsi(ticker, current_price=None, period=14):
    closes = get_recent_closes(ticker)
    if not closes:
        return None
    if current_price is not None:
        closes = closes[:-1] + [current_price]  # 마지막 종가를 실시간가로 대체
    return calculate_rsi(closes, period=period)


def render_market_overview():
    # ⚡ 아래에서 순서대로 하나씩 불러오면 20개 가까운 외부 요청이 직렬로 쌓여서
    # 느려지므로, 먼저 전부 동시에(병렬로) 미리 가져와 캐시를 채워둠.
    # 이후 코드는 그대로 각 함수를 다시 호출하지만, 캐시에 이미 있어서 즉시 반환됨.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as _warm_pool:
        _warm_jobs = [
            _warm_pool.submit(get_index_data, "USDKRW=X"),
            _warm_pool.submit(get_index_data, "^VIX"),
            _warm_pool.submit(get_index_data, "SHY"),
            _warm_pool.submit(get_index_data, "^TNX"),
            _warm_pool.submit(get_cnn_fear_greed),
            _warm_pool.submit(get_global_m2),
            _warm_pool.submit(get_finnhub_quote, "UUP"),
        ]
        concurrent.futures.wait(_warm_jobs, timeout=8)

    # 2. 지수 티커 바 2줄 (접기 없이 항상 표시)
    #    1줄: 코스피·S&P500·나스닥·반도체·골드
    #    2줄: 원유·빅스·환율·국채(2년/10년)·공포탐욕
    def ticker_cell(name, data, unit="", suffix="", decimals=2, extra="", rsi=None):
        if not data:
            return (
                '<div style="flex:1 1 0;min-width:0;padding:8px 6px;border-right:1px solid #222;text-align:center;">'
                f'<div style="font-size:11px;color:#aaa;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{name}</div>'
                '<div style="font-size:12px;color:#666;margin-top:2px;">⏳</div>'
                '</div>'
            )
        pct_color = "#ff4d4d" if data['change_pct'] >= 0 else "#4d94ff"
        arrow = "▲" if data['change_pct'] >= 0 else "▼"
        rsi_html = ""
        if rsi is not None:
            # RSI 색상: 70이상 과매수(빨강), 30이하 과매도(파랑), 중간은 회색
            if rsi >= 70: rsi_color = "#ff4d4d"
            elif rsi <= 30: rsi_color = "#4d94ff"
            else: rsi_color = "#999"
            rsi_html = f'<div style="font-size:10px;color:#888;margin-top:1px;white-space:nowrap;">RSI <span style="color:{rsi_color};font-weight:700;">{rsi:.0f}</span></div>'
        return (
            '<div style="flex:1 1 0;min-width:0;padding:8px 6px;border-right:1px solid #222;text-align:center;">'
            f'<div style="font-size:11px;color:#aaa;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{name}</div>'
            f'<div style="font-size:14px;font-weight:800;color:#fff;margin-top:2px;white-space:nowrap;">{unit}{data["current"]:,.{decimals}f}{suffix}</div>'
            f'<div style="font-size:11px;font-weight:700;color:{pct_color};white-space:nowrap;">{arrow} {abs(data["change_pct"]):.2f}%</div>'
            f'{rsi_html}{extra}'
            '</div>'
        )

    # 데이터 조회 (5개만: 환율·공포탐욕·글로벌M2·빅스·미국국채)
    krw = get_index_data("USDKRW=X")
    vix_data = get_index_data("^VIX") or {"current": 14.50, "change_pct": 1.20, "high": None, "drop": None}
    shy_data = get_index_data("SHY") or {"current": 4.12, "change_pct": 0.05, "high": None, "drop": None}
    if shy_data['current'] > 15: shy_data['current'] = 4.12
    tnx_data = get_index_data("^TNX") or {"current": 4.37, "change_pct": -0.12, "high": None, "drop": None}
    if tnx_data['current'] > 15: tnx_data['current'] = tnx_data['current'] / 10
    fg_score, fg_status = get_cnn_fear_greed()
    hy = get_high_yield_spread()
    m2 = get_global_m2()

    # 빅스 상태 태그
    vix_v = vix_data['current']
    if vix_v < 15: vix_msg, vix_color = "안정", "#4dff4d"
    elif vix_v < 20: vix_msg, vix_color = "유의", "#ffff4d"
    elif vix_v < 30: vix_msg, vix_color = "경계", "#ff944d"
    else: vix_msg, vix_color = "위험", "#ff4d4d"
    vix_extra = f'<div style="margin-top:1px;"><span style="font-size:10px;color:{vix_color};">{vix_msg}</span></div>'

    # 국채: 한 칸에 2년/10년 같이
    shy_color = "#ff4d4d" if shy_data['change_pct'] >= 0 else "#4d94ff"
    tnx_color = "#ff4d4d" if tnx_data['change_pct'] >= 0 else "#4d94ff"
    bond_cell = (
        '<div style="flex:1 1 0;min-width:0;padding:8px 6px;border-right:1px solid #222;text-align:center;">'
        '<div style="font-size:11px;color:#aaa;white-space:nowrap;">🇺🇸 국채</div>'
        f'<div style="font-size:12px;font-weight:700;color:#fff;margin-top:2px;white-space:nowrap;">2년 <span style="color:{shy_color};">{shy_data["current"]:.2f}%</span></div>'
        f'<div style="font-size:12px;font-weight:700;color:#fff;white-space:nowrap;">10년 <span style="color:{tnx_color};">{tnx_data["current"]:.2f}%</span></div>'
        '</div>'
    )

    # 공포탐욕
    en_map = {"극단적 공포": "Extreme Fear", "공포": "Fear", "중립": "Neutral", "탐욕": "Greed", "극단적 탐욕": "Extreme Greed"}
    en_label = en_map.get(fg_status, fg_status)
    fg_cell = (
        '<div style="flex:1 1 0;min-width:0;padding:8px 6px;text-align:center;">'
        '<div style="font-size:11px;color:#aaa;white-space:nowrap;">공포·탐욕</div>'
        f'<div style="font-size:14px;font-weight:800;color:#fff;margin-top:2px;">{fg_score}</div>'
        f'<div style="font-size:10px;font-weight:700;color:#ccc;white-space:nowrap;">{en_label}</div>'
        '</div>'
    )

    # 달러인덱스 셀 (UUP = Invesco DB US Dollar Index ETF, Finnhub 실시간)
    dxy = get_finnhub_quote("UUP")
    if dxy:
        dxy_color = "#ff4d4d" if dxy["change_pct"] >= 0 else "#4d94ff"
        dxy_arrow = "▲" if dxy["change_pct"] >= 0 else "▼"
        dxy_cell = (
            '<div style="flex:1 1 0;min-width:0;padding:8px 6px;border-right:1px solid #222;text-align:center;">'
            '<div style="font-size:11px;color:#aaa;white-space:nowrap;">달러인덱스</div>'
            f'<div style="font-size:14px;font-weight:800;color:#fff;margin-top:2px;white-space:nowrap;">${dxy["current"]:,.2f}</div>'
            f'<div style="font-size:11px;font-weight:700;color:{dxy_color};white-space:nowrap;">{dxy_arrow} {abs(dxy["change_pct"]):.2f}%</div>'
            '</div>'
        )
    else:
        dxy_cell = (
            '<div style="flex:1 1 0;min-width:0;padding:8px 6px;border-right:1px solid #222;text-align:center;">'
            '<div style="font-size:11px;color:#aaa;white-space:nowrap;">달러인덱스</div>'
            '<div style="font-size:12px;color:#666;margin-top:2px;">⏳</div></div>'
        )

    # 환율 셀
    krw_cell = ticker_cell("환율", krw, unit="₩", decimals=1)
    # 빅스 셀
    vix_cell = ticker_cell("빅스 VIX", vix_data, unit="", decimals=2, extra=vix_extra)

    # 한 줄: 환율 · 공포탐욕 · 달러인덱스 · 빅스 · 국채  (M2는 아래 접이식 차트로 이동)
    row = (
        '<div style="display:flex;background-color:#111;border-radius:8px;overflow:hidden;margin-bottom:6px;flex-wrap:wrap;">'
        + krw_cell + fg_cell + dxy_cell + vix_cell + bond_cell
        + '</div>'
    )
    st.markdown(row, unsafe_allow_html=True)

    # 글로벌 M2 큰 추세 차트 (접이식)
    if m2 and m2.get("trend") and len(m2["trend"]) >= 2:
        with st.expander("글로벌 M2 추세 차트 (최근 24개월)"):
            trend = m2["trend"]
            big = make_sparkline_svg(trend, width=340, height=120,
                                     color="#4dd2ff" if (m2.get("yoy") or 0) >= 0 else "#ff6f4d")
            first_v, last_v = trend[0], trend[-1]
            chg = ((last_v - first_v) / first_v * 100) if first_v else 0
            chg_color = "#ff4d4d" if chg >= 0 else "#4d94ff"
            st.markdown(
                '<div style="background:#0d0d0d;border-radius:10px;padding:14px 16px;">'
                '<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;">'
                f'<span style="font-size:14px;font-weight:700;color:#fff;">글로벌 M2 통화량</span>'
                f'<span style="font-size:18px;font-weight:800;color:#fff;">${last_v:,.1f}T</span>'
                '</div>'
                f'<div style="height:120px;">{big}</div>'
                '<div style="display:flex;justify-content:space-between;font-size:11px;color:#888;margin-top:6px;">'
                f'<span>24개월 전 ${first_v:,.1f}T</span>'
                f'<span style="color:{chg_color};font-weight:700;">24개월 {"▲" if chg>=0 else "▼"} {abs(chg):.1f}%</span>'
                '</div>'
                '<div style="font-size:10px;color:#666;margin-top:8px;">※ 미국 M2 + 유로존 M2 합산 (FRED). 위험자산 흐름의 선행 지표로 활용됨.</div>'
                '</div>',
                unsafe_allow_html=True
            )


render_market_overview()


# ==========================================================
# 📂 5. 포트폴리오 기능 (신규 추가)
# ==========================================================

if "portfolios" not in st.session_state:
    st.session_state.portfolios = load_portfolios()   # {} 대신 파일에서 불러오기
    # 처음 실행 시(저장된 포트폴리오가 하나도 없을 때) 기본 4개를 미리 만들어둠
    if not st.session_state.portfolios:
        st.session_state.portfolios = {
            "1. 연금": [],
            "2. ISA": [],
            "3. 직투": [],
            "4. 코인": [],
        }
        save_portfolios()

if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_watchlist()   # 관심종목 (보유 여부와 무관한 별도 리스트)

if "expanded_state" not in st.session_state:
    st.session_state.expanded_state = {}   # 포트폴리오별 접기/펼치기 상태

if "section_expanded" not in st.session_state:
    st.session_state.section_expanded = {"watchlist": True, "holdings": True}   # 관심종목/보유종목 섹션 접기 상태


# 한국 종목(.KS/.KQ) 한글 종목명 조회 (네이버 금융)
@st.cache_data(ttl=86400)
def get_korean_name(ticker):
    try:
        code = ticker.split(".")[0]
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=3)
        soup = BeautifulSoup(res.text, "html.parser")
        title_tag = soup.select_one("div.wrap_company h2 a")
        if title_tag and title_tag.text.strip():
            return title_tag.text.strip()
    except Exception:
        pass
    return None


# 종목 검색 (yfinance 내장 검색 기능 사용)
# 자주 검색되는 국내 대형주 한글명 -> 티커 매핑
# (yfinance 검색 API가 한글 회사명은 잘 못 찾아서, 흔한 이름은 미리 보정)
KOREAN_STOCK_ALIASES = {
    "삼성전자": "005930.KS", "삼성전자우": "005935.KS", "sk하이닉스": "000660.KS",
    "에스케이하이닉스": "000660.KS", "카카오": "035720.KS", "네이버": "035420.KS",
    "naver": "035420.KS", "현대차": "005380.KS", "현대자동차": "005380.KS",
    "기아": "000270.KS", "lg에너지솔루션": "373220.KS", "삼성바이오로직스": "207940.KS",
    "셀트리온": "068270.KS", "posco홀딩스": "005490.KS", "포스코홀딩스": "005490.KS",
    "삼성sdi": "006400.KS", "lg화학": "051910.KS", "현대모비스": "012330.KS",
    "kb금융": "105560.KS", "신한지주": "055550.KS", "카카오뱅크": "323410.KS",
    "하나금융지주": "086790.KS", "lg전자": "066570.KS", "삼성물산": "028260.KS",
    "sk이노베이션": "096770.KS", "두산에너빌리티": "034020.KS",
    "한화에어로스페이스": "012450.KS", "삼성생명": "032830.KS", "크래프톤": "259960.KS",
    "에코프로": "086520.KQ", "에코프로비엠": "247540.KQ", "알테오젠": "196170.KQ",
    "삼성전기": "009150.KS", "sk텔레콤": "017670.KS", "kt": "030200.KS",
    "포스코퓨처엠": "003670.KS", "한국전력": "015760.KS", "삼성화재": "000810.KS",
    "미래에셋증권": "006800.KS", "우리금융지주": "316140.KS", "hd현대중공업": "329180.KS",
}


# 미국(해외) 티커 -> 한글 표시명 매핑
# (카드에서 "Micron Technology, Inc." 같은 긴 영문 대신 "마이크론"처럼 짧은 한글명을 보여주기 위함)
# 매핑에 없는 티커는 검색 시 저장된 원래 이름(영문)을 그대로 사용함
US_STOCK_KOREAN_NAMES = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "GOOGL": "알파벳(구글)", "GOOG": "알파벳(구글)",
    "AMZN": "아마존", "NVDA": "엔비디아", "META": "메타", "TSLA": "테슬라",
    "MU": "마이크론", "AVGO": "브로드컴", "AMD": "AMD", "INTC": "인텔",
    "QCOM": "퀄컴", "TSM": "TSMC", "NFLX": "넷플릭스", "BABA": "알리바바",
    "ORCL": "오라클", "CRM": "세일즈포스", "ADBE": "어도비", "PYPL": "페이팔",
    "DIS": "디즈니", "KO": "코카콜라", "PEP": "펩시코", "MCD": "맥도날드",
    "NKE": "나이키", "SBUX": "스타벅스", "V": "비자", "MA": "마스터카드",
    "JPM": "JP모건", "BAC": "뱅크오브아메리카", "WMT": "월마트", "COST": "코스트코",
    "HD": "홈디포", "XOM": "엑슨모빌", "CVX": "셰브런", "BA": "보잉",
    "GE": "GE", "F": "포드", "GM": "GM", "UBER": "우버",
    "ABNB": "에어비앤비", "PLTR": "팔란티어", "ASML": "ASML", "ARM": "ARM",
}


def get_display_name(ticker, fallback_name):
    """미국 주식은 짧은 한글 표시명이 있으면 그걸 쓰고, 없으면 기존 이름 사용"""
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return fallback_name
    return US_STOCK_KOREAN_NAMES.get(ticker.upper(), fallback_name)


def fmt_money(value, is_usd, decimals=None):
    """달러는 '95.12$', 원화는 '1,234원' 형식으로 반환.
    decimals는 달러에만 적용됨 (평단가/현재가처럼 소수점이 중요한 값에 사용).
    원화(원)는 소수점 단위가 없으므로 항상 정수로 표시.
    &#36;은 Streamlit markdown이 '$'를 LaTeX 수식 기호로 잘못 해석하는 걸 막기 위한 HTML 엔티티."""
    if is_usd:
        d = 0 if decimals is None else decimals
        return f"{value:,.{d}f}&#36;"
    # 원화는 소수점 없이 항상 정수
    return f"{value:,.0f}원"


def fmt_krw(value):
    """원화환산 값을 '1,500원' 형식으로 반환 (₩ 기호 없이 숫자+원)"""
    return f"{value:,.0f}원"


def combine_currency(usd_html, krw_txt):
    """통화 토글에 따라 달러 또는 원화 하나만 표시.
    '$ 달러' 모드면 달러만, '₩ 원화' 모드면 원화만 보여줌 (둘 다 표시 안 함 → 깔끔)."""
    show_krw = st.session_state.get("currency_mode_radio", "$ 달러") == "₩ 원화"
    if show_krw:
        return f'<span>{krw_txt}</span>'
    return usd_html


@st.cache_data(ttl=3600)
def search_naver_autocomplete(query):
    """네이버 자동완성 API로 한글 종목명 검색 (yfinance가 못 찾는 한글명 보완)"""
    try:
        url = "https://ac.stock.naver.com/ac"
        params = {"q": query, "target": "stock,index,marketindicator"}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, params=params, headers=headers, timeout=3)
        data = res.json()
        results = []
        for group in data.get("items", []):
            for item in group:
                # 네이버 응답 형식: [종목명, 시장구분/기타, 코드, 타입] 순서가 버전에 따라 다를 수 있어 방어적으로 파싱
                if not isinstance(item, list) or len(item) < 3:
                    continue
                name_field = item[0]
                code_field = item[2] if len(item) > 2 else None
                market_field = item[1] if len(item) > 1 else ""
                if not code_field or not str(code_field).isdigit():
                    continue
                suffix = ".KQ" if "KOSDAQ" in str(market_field).upper() else ".KS"
                results.append({"symbol": f"{code_field}{suffix}", "name": name_field})
        return results
    except Exception:
        return []


@st.cache_data(ttl=300)
def search_stock(query):
    if not query or len(query.strip()) < 1:
        return []

    results = []
    seen_symbols = set()
    q_lower = query.strip().lower()
    q_upper = query.strip().upper()

    # 0) 흔한 영단어와 겹치는 티커(RAM, USD 등)는 야후 검색 API가 종목으로
    #    잘 인식하지 못하는 경우가 많아, 자주 쓰는 레버리지 ETF는 직접 매칭시켜 둠
    KNOWN_TICKER_FALLBACK = {
        "RAM": "Roundhill T-REX 2X Long DRAM Daily Target ETF",
        "USD": "ProShares Ultra Semiconductors",
        "SOXL": "Direxion Daily Semiconductor Bull 3X",
        "SOXS": "Direxion Daily Semiconductor Bear 3X",
        "TQQQ": "ProShares UltraPro QQQ",
        "SQQQ": "ProShares UltraPro Short QQQ",
        "QLD": "ProShares Ultra QQQ",
        "SSO": "ProShares Ultra S&P500",
        "SPXL": "Direxion Daily S&P 500 Bull 3X",
    }
    if q_upper in KNOWN_TICKER_FALLBACK and q_upper not in seen_symbols:
        results.append({"symbol": q_upper, "name": KNOWN_TICKER_FALLBACK[q_upper]})
        seen_symbols.add(q_upper)

    # 1) 흔한 국내 대형주 한글명 매핑 우선 확인
    for alias, symbol in KOREAN_STOCK_ALIASES.items():
        if q_lower in alias or alias in q_lower:
            if symbol not in seen_symbols:
                kr_name = get_korean_name(symbol) or symbol
                results.append({"symbol": symbol, "name": kr_name})
                seen_symbols.add(symbol)

    # 2) yfinance 검색 (영문명/티커에 강함)
    #    yf.Search()는 타임아웃이 없어서 야후가 느리게 응답하면 페이지 전체가
    #    그 시간만큼 멈춰버림 (다른 부분엔 문제없는데 검색만 하면 화면 전체가
    #    "연결중"으로 보이는 원인이 이거였음) -> 3초 안에 안 끝나면 그냥 포기하고 넘어감
    _search_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        _search_future = _search_pool.submit(lambda: yf.Search(query, max_results=8).quotes)
        quotes = _search_future.result(timeout=3)
        for q in quotes:
            symbol = q.get("symbol")
            name = q.get("shortname") or q.get("longname") or symbol
            if symbol and symbol not in seen_symbols:
                if symbol.endswith(".KS") or symbol.endswith(".KQ"):
                    kr_name = get_korean_name(symbol)
                    if kr_name:
                        name = kr_name
                results.append({"symbol": symbol, "name": name})
                seen_symbols.add(symbol)
    except Exception:
        pass
    finally:
        # wait=False: 시간 초과된 스레드가 백그라운드에서 알아서 끝나도록 두고,
        # 여기서 그 스레드 종료까지 기다리지 않고 바로 다음으로 진행함
        _search_pool.shutdown(wait=False)

    # 3) 네이버 자동완성 (한글 종목명 보완, 위 두 방법으로 못 찾았을 때)
    if not results:
        for r in search_naver_autocomplete(query):
            if r["symbol"] not in seen_symbols:
                results.append(r)
                seen_symbols.add(r["symbol"])

    return results[:8]


# ==========================================
# 💹 국내 종목 실시간가 (네이버금융 크롤링)
#   -> 상단 코스피/코스닥과 동일한 방식으로,
#      개별 국내 종목도 지연 없이 실시간가를 가져옴
# ==========================================
@st.cache_data(ttl=60)
def get_naver_stock_price(ticker):
    code = ticker.split(".")[0]
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=3)
        soup = BeautifulSoup(res.text, "html.parser")
        price_tag = soup.select_one("p.no_today span.blind")
        if price_tag:
            return float(price_tag.text.replace(",", ""))
    except Exception:
        pass
    return None


# ==========================================
# 💱 현재 원/달러 환율 (직투계좌 원화환산 · 환차익 계산용)
# ==========================================
@st.cache_data(ttl=120)
def get_usd_krw_rate():
    try:
        live = get_yf_live_price("USDKRW=X")
        if live:
            return float(live)
    except Exception:
        pass
    try:
        df = yf.Ticker("USDKRW=X").history(period="5d")
        if not df.empty:
            return float(df['Close'].iloc[-1])
    except Exception:
        pass
    return None


# 해외 종목 실시간에 더 가까운 시세 조회 (fast_info 우선, 실패 시 history()로 폴백)


# ==========================================
# 🌙🌆 미국 주식 프리마켓 / 애프터마켓 시세
#   -> 뉴욕시간 기준으로 현재 세션(프리/정규/애프터/휴장) 판단 후,
#      yfinance info의 확장시간 필드를 우선 사용하고,
#      실패 시 1분봉(prepost=True) 마지막 데이터로 추정
# ==========================================
def get_us_market_session():
    """현재 시각 기준 미국 장이 프리/정규/애프터/휴장 중 어디인지 판단 (뉴욕시간 기준)"""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return "closed"
    t = now_et.time()
    if dtime(4, 0) <= t < dtime(9, 30):
        return "pre"
    elif dtime(9, 30) <= t < dtime(16, 0):
        return "regular"
    elif dtime(16, 0) <= t < dtime(20, 0):
        return "post"
    return "closed"


@st.cache_data(ttl=60)
def get_extended_hours_price(ticker):
    """미국 주식 프리마켓/애프터마켓 시세.
    1순위: yfinance info의 preMarketPrice/postMarketPrice
    2순위: 1분봉(prepost=True) 마지막 데이터로 추정"""
    session = get_us_market_session()
    if session not in ("pre", "post"):
        return None

    try:
        info = yf.Ticker(ticker).get_info()
        if session == "pre":
            price = info.get("preMarketPrice")
            change_pct = info.get("preMarketChangePercent")
        else:
            price = info.get("postMarketPrice")
            change_pct = info.get("postMarketChangePercent")
        if price:
            return {"price": float(price), "change_pct": float(change_pct or 0.0), "session": session}
    except Exception:
        pass

    try:
        intraday = yf.Ticker(ticker).history(period="1d", interval="1m", prepost=True)
        daily = yf.Ticker(ticker).history(period="2d", interval="1d")
        if not intraday.empty and not daily.empty:
            base_price = float(daily['Close'].iloc[-1])
            last_price = float(intraday['Close'].iloc[-1])
            change_pct = (last_price - base_price) / base_price * 100
            return {"price": last_price, "change_pct": change_pct, "session": session}
    except Exception:
        pass
    return None


# 현재가 조회 (포트폴리오 손익 계산용)
#  - 국내 종목(.KS/.KQ): 네이버금융 실시간가 우선 사용 (지연 없음)
#  - 해외 종목: fast_info(실시간에 더 가까움) 우선, 실패 시 일봉 종가로 폴백
@st.cache_data(ttl=60)
def get_current_price(ticker):
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        price = get_naver_stock_price(ticker)
        if price is not None:
            return price
    else:
        # 미국 종목은 Finnhub 먼저 (실시간 + 빠름), 실패 시 yfinance
        fh = get_finnhub_quote(ticker)
        if fh is not None:
            return fh["current"]
        live = get_yf_live_price(ticker)
        if live is not None:
            return live
    try:
        df = yf.Ticker(ticker).history(period="5d")
        if not df.empty:
            return float(df['Close'].iloc[-1])
    except Exception:
        pass
    return None


# ==========================================
# 📈 RSI(14일) 계산
#   -> 최근 종가에 실시간가를 반영해서 최대한 실시간에
#      가깝게 계산 (단순 이동평균 기반 RSI)


@st.dialog("새 포트폴리오 만들기")
def create_portfolio_dialog():
    name = st.text_input("포트폴리오 제목", placeholder="예: 미국 성장주 포트폴리오")
    if st.button("생성", use_container_width=True):
        name = name.strip()
        if not name:
            st.warning("제목을 입력해주세요.")
        elif name in st.session_state.portfolios:
            st.warning("이미 존재하는 이름입니다.")
        else:
            st.session_state.portfolios[name] = []
            save_portfolios()   # ★ 저장
            st.rerun()


@st.dialog("종목 추가")
def add_stock_dialog(portfolio_name):
    st.caption("💡 종목명/티커 입력 후 Enter를 누르면 검색됩니다")
    query = st.text_input("종목명 또는 티커 검색", placeholder="예: 삼성전자, AAPL, 엔비디아",
                          key=f"search_q_{portfolio_name}")

    selected_symbol = None
    selected_name = None

    if query and len(query.strip()) >= 1:
        with st.spinner("검색 중..."):
            results = search_stock(query.strip())
        if results:
            option_labels = [f"{r['name']} ({r['symbol']})" for r in results]
            picked = st.selectbox("검색결과에서 선택", option_labels)
            picked_idx = option_labels.index(picked)
            selected_symbol = results[picked_idx]["symbol"]
            selected_name = results[picked_idx]["name"]
        else:
            st.info("검색 결과가 없습니다. 정확한 티커(예: AAPL, 005930.KS)를 입력해보세요.")
            manual_ticker = st.text_input("직접 티커 입력 (검색 결과가 없을 때)")
            if manual_ticker:
                selected_symbol = manual_ticker.strip().upper()
                selected_name = selected_symbol
                if selected_symbol.endswith(".KS") or selected_symbol.endswith(".KQ"):
                    kr_name = get_korean_name(selected_symbol)
                    if kr_name:
                        selected_name = kr_name

    st.divider()
    qty = st.number_input("수량", min_value=0.0, step=1.0, format="%.4f")
    avg_price = st.number_input("평단가 (매수 평균단가)", min_value=0.0, step=0.0001, format="%.4f")
    target_weight = st.number_input("목표비중 (%)", min_value=0.0, max_value=100.0, step=1.0, format="%.0f")

    is_usd = bool(selected_symbol) and not (selected_symbol.endswith(".KS") or selected_symbol.endswith(".KQ"))

    buy_fx_rate = 0.0
    if is_usd:
        cur_fx = get_usd_krw_rate() or 1350.0
        buy_fx_rate = st.number_input(
            "매수 시 환율 (원/달러)", min_value=0.0, step=1.0, format="%.0f", value=float(round(cur_fx)),
            help="나중에 환차익을 따로 보고 싶으면 매수 당시 환율을 입력해두세요. 모르면 현재 환율 그대로 두면 돼요."
        )

    if qty > 0 and avg_price > 0:
        st.caption(f"매수금액: {fmt_money(qty * avg_price, is_usd)}")

    if st.button("추가하기", use_container_width=True, type="primary"):
        if not selected_symbol:
            st.warning("종목을 검색해서 선택해주세요.")
        elif qty <= 0 or avg_price <= 0:
            st.warning("수량과 평단가를 입력해주세요.")
        else:
            new_holding = {
                "ticker": selected_symbol,
                "name": selected_name,
                "qty": qty,
                "avg_price": avg_price,
                "target_weight": target_weight
            }
            if is_usd and buy_fx_rate > 0:
                new_holding["buy_fx_rate"] = buy_fx_rate
            st.session_state.portfolios[portfolio_name].append(new_holding)
            save_portfolios()   # ★ 저장
            st.rerun()


@st.dialog("종목 수정")
def edit_stock_dialog(portfolio_name, index):
    holding = st.session_state.portfolios[portfolio_name][index]
    display_name = get_display_name(holding["ticker"], holding["name"])

    st.markdown(f"**{display_name}** ({holding['ticker']})")

    is_usd = not (holding["ticker"].endswith(".KS") or holding["ticker"].endswith(".KQ"))

    new_qty = st.number_input("수량", min_value=0.0, step=1.0, format="%.4f", value=float(holding["qty"]))
    new_avg_price = st.number_input("평단가 (매수 평균단가)", min_value=0.0, step=0.0001, format="%.4f", value=float(holding["avg_price"]))
    new_target_weight = st.number_input("목표비중 (%)", min_value=0.0, max_value=100.0, step=1.0, format="%.0f", value=float(holding.get("target_weight", 0.0) or 0.0))

    new_buy_fx_rate = holding.get("buy_fx_rate", 0.0) or 0.0
    if is_usd:
        cur_fx = get_usd_krw_rate() or 1350.0
        default_fx = new_buy_fx_rate if new_buy_fx_rate > 0 else round(cur_fx)
        new_buy_fx_rate = st.number_input(
            "매수 시 환율 (원/달러)", min_value=0.0, step=1.0, format="%.0f", value=float(default_fx),
            help="환차익을 따로 보고 싶으면 매수 당시 환율을 입력해두세요."
        )

    if new_qty > 0 and new_avg_price > 0:
        st.caption(f"매수금액: {fmt_money(new_qty * new_avg_price, is_usd)}")

    btn_cols = st.columns([1, 1])
    with btn_cols[0]:
        if st.button("저장", use_container_width=True, type="primary"):
            if new_qty <= 0 or new_avg_price <= 0:
                st.warning("수량과 평단가를 입력해주세요.")
            else:
                st.session_state.portfolios[portfolio_name][index]["qty"] = new_qty
                st.session_state.portfolios[portfolio_name][index]["avg_price"] = new_avg_price
                st.session_state.portfolios[portfolio_name][index]["target_weight"] = new_target_weight
                if is_usd:
                    st.session_state.portfolios[portfolio_name][index]["buy_fx_rate"] = new_buy_fx_rate
                save_portfolios()
                st.rerun()
    with btn_cols[1]:
        if st.button("삭제", use_container_width=True):
            st.session_state.portfolios[portfolio_name].pop(index)
            save_portfolios()
            st.rerun()


@st.dialog("추가 매수")
def add_more_dialog(portfolio_name, index):
    """기존 보유 종목에 추가 매수. 평단가·수량·매수환율을 가중평균으로 누적 반영."""
    holding = st.session_state.portfolios[portfolio_name][index]
    display_name = get_display_name(holding["ticker"], holding["name"])
    is_usd = not (holding["ticker"].endswith(".KS") or holding["ticker"].endswith(".KQ"))

    old_qty = float(holding["qty"])
    old_avg = float(holding["avg_price"])
    old_fx = float(holding.get("buy_fx_rate", 0.0) or 0.0)

    st.markdown(f"**{display_name}** ({holding['ticker']})")
    st.caption(f"현재 보유: {old_qty:,.4f}주 · 평단가 {fmt_money(old_avg, is_usd, decimals=2 if is_usd else None)}"
               + (f" · 매수환율 {old_fx:,.0f}원" if (is_usd and old_fx) else ""))
    st.markdown("<div style='border-top:1px solid #333;margin:8px 0;'></div>", unsafe_allow_html=True)
    st.markdown("**이번에 추가로 매수한 내역**")

    add_qty = st.number_input("추가 매수 수량", min_value=0.0, step=1.0, format="%.4f")
    add_price = st.number_input("추가 매수 단가", min_value=0.0, step=0.0001, format="%.4f")

    add_fx = 0.0
    if is_usd:
        cur_fx = get_usd_krw_rate() or 1350.0
        add_fx = st.number_input(
            "추가 매수 시 환율 (원/달러)", min_value=0.0, step=1.0, format="%.0f", value=float(round(cur_fx)),
            help="이번 추가 매수 시점의 환율. 기존 매수환율과 가중평균으로 합산됩니다."
        )

    # 합산 결과 미리보기
    if add_qty > 0 and add_price > 0:
        total_qty = old_qty + add_qty
        new_avg = (old_qty * old_avg + add_qty * add_price) / total_qty
        preview = (
            f"합산 후 → 수량 {total_qty:,.4f}주 · 평단가 {fmt_money(new_avg, is_usd, decimals=2 if is_usd else None)}"
        )
        if is_usd and add_fx > 0:
            # 매수환율도 매수금액(달러) 기준 가중평균
            old_cost = old_qty * old_avg
            add_cost = add_qty * add_price
            base_fx = old_fx if old_fx > 0 else add_fx
            new_fx = (old_cost * base_fx + add_cost * add_fx) / (old_cost + add_cost)
            preview += f" · 매수환율 {new_fx:,.0f}원"
        st.success(preview)

    btn_cols = st.columns([1, 1])
    with btn_cols[0]:
        if st.button("추가매수 반영", use_container_width=True, type="primary"):
            if add_qty <= 0 or add_price <= 0:
                st.warning("추가 매수 수량과 단가를 입력해주세요.")
            else:
                total_qty = old_qty + add_qty
                new_avg = (old_qty * old_avg + add_qty * add_price) / total_qty
                st.session_state.portfolios[portfolio_name][index]["qty"] = total_qty
                st.session_state.portfolios[portfolio_name][index]["avg_price"] = new_avg
                if is_usd and add_fx > 0:
                    old_cost = old_qty * old_avg
                    add_cost = add_qty * add_price
                    base_fx = old_fx if old_fx > 0 else add_fx
                    new_fx = (old_cost * base_fx + add_cost * add_fx) / (old_cost + add_cost)
                    st.session_state.portfolios[portfolio_name][index]["buy_fx_rate"] = new_fx
                save_portfolios()
                st.rerun()
    with btn_cols[1]:
        if st.button("취소", use_container_width=True):
            st.rerun()


def compute_portfolio_rows(holdings):
    rows = []
    total_buy_amount = 0.0
    total_eval_amount = 0.0

    # 직투(달러) 계좌의 원화환산 합계 및 환차익 계산용
    cur_fx = get_usd_krw_rate()
    usd_buy_krw_total = 0.0
    usd_eval_krw_total = 0.0
    usd_fx_gain_total = 0.0
    has_usd_fx_data = False
    krw_buy_total = 0.0   # 국내(원화) 종목 매수 합계
    krw_eval_total = 0.0  # 국내(원화) 종목 평가 합계

    for h in holdings:
        current_price = get_current_price(h["ticker"])
        rsi_value = get_rsi(h["ticker"], current_price)
        buy_amount = h["qty"] * h["avg_price"]
        eval_amount = h["qty"] * current_price if current_price is not None else None

        total_buy_amount += buy_amount
        if eval_amount is not None:
            total_eval_amount += eval_amount

        is_usd = not (h["ticker"].endswith(".KS") or h["ticker"].endswith(".KQ"))
        buy_fx_rate = h.get("buy_fx_rate", 0.0) or 0.0
        effective_buy_fx = buy_fx_rate if buy_fx_rate > 0 else cur_fx
        if is_usd and cur_fx and eval_amount is not None:
            has_usd_fx_data = True
            usd_buy_krw_total += buy_amount * effective_buy_fx
            usd_eval_krw_total += eval_amount * cur_fx
            usd_fx_gain_total += h["qty"] * h["avg_price"] * (cur_fx - effective_buy_fx)
        elif not is_usd:
            # 국내 종목: 원화 그대로 계좌 원화 합계에 더함
            krw_buy_total += buy_amount
            if eval_amount is not None:
                krw_eval_total += eval_amount

        # 52주 신고가 (신고가 대비 하락률 표시용)
        high_52w = None
        try:
            _stats = get_year_history_stats(h["ticker"])
            if _stats:
                high_52w = _stats.get("high_52w")
        except Exception:
            high_52w = None

        rows.append({
            **h,
            "current_price": current_price,
            "rsi": rsi_value,
            "high_52w": high_52w,
            "buy_amount": buy_amount,
            "eval_amount": eval_amount,
        })

    fx_summary = {
        "has_data": has_usd_fx_data,
        "buy_krw": usd_buy_krw_total,
        "eval_krw": usd_eval_krw_total,
        "fx_gain": usd_fx_gain_total,
        "cur_fx": cur_fx,
        # 계좌 전체 원화 합계 (해외 환산분 + 국내 원화분) — 합산에 사용
        "total_buy_krw": usd_buy_krw_total + krw_buy_total,
        "total_eval_krw": usd_eval_krw_total + krw_eval_total,
    }
    return rows, total_buy_amount, total_eval_amount, fx_summary


@st.cache_data(ttl=300)
def get_prev_close(ticker):
    """전일 종가 조회 (당일 등락률 계산용)"""
    try:
        df = yf.Ticker(ticker).history(period="5d")
        if not df.empty and len(df) >= 2:
            return float(df['Close'].iloc[-2])
        elif not df.empty:
            return float(df['Close'].iloc[-1])
    except Exception:
        pass
    return None


def render_ticker_cards(items):
    """items: [(ticker, name), ...] 티커/현재가/당일 등락률을, 이름 길이에 맞는 폭의 칩으로
    flex-wrap 배치 (종목이 많아져도 빈 공간 없이 줄바꿈되며 촘촘하게 배치됨)"""
    if not items:
        return

    chips = []
    for ticker, name in items:
        display_name = get_display_name(ticker, name)
        current = get_current_price(ticker)
        prev = get_prev_close(ticker)
        is_usd_ticker = not (ticker.endswith(".KS") or ticker.endswith(".KQ"))

        if current is not None and prev not in (None, 0):
            change_pct = (current - prev) / prev * 100
            color = "#ff4d4d" if change_pct >= 0 else "#4d94ff"
            arrow = "▲" if change_pct >= 0 else "▼"
            price_html = fmt_money(current, is_usd_ticker)
            change_html = f"<span style='color:{color};font-weight:bold;font-size:12px;'>{arrow} {abs(change_pct):.2f}%</span>"
        elif current is not None:
            price_html = fmt_money(current, is_usd_ticker)
            change_html = "<span style='color:#666;font-size:11px;'>-</span>"
        else:
            price_html = "⏳"
            change_html = ""

        # 🌙프리 / 🌆애프터 시세 (미국 주식만 해당, 해당 시간대에만 표시)
        # 주의: HTML은 반드시 들여쓰기 없는 한 줄 문자열로 만들어야 함.
        # 앞에 공백(스페이스 4칸 이상)이 붙으면 마크다운이 "코드블록"으로 오인해서
        # HTML 태그가 그대로 텍스트로 노출되는 문제가 발생함.
        ext_html = ""
        if not (ticker.endswith(".KS") or ticker.endswith(".KQ")):
            ext = get_extended_hours_price(ticker)
            if ext:
                ext_color = "#ff4d4d" if ext["change_pct"] >= 0 else "#4d94ff"
                ext_arrow = "▲" if ext["change_pct"] >= 0 else "▼"
                ext_label = "🌙프리" if ext["session"] == "pre" else "🌆애프터"
                ext_html = (
                    '<div style="margin-top:3px;padding-top:3px;border-top:1px dashed #333;">'
                    f'<span style="font-size:9px;color:#ffaa4d;">{ext_label}</span>'
                    f'<span style="font-size:13px;color:#ffffff;font-weight:700;margin-left:2px;">{ext["price"]:,.2f}&#36;</span>'
                    f'<span style="font-size:11px;color:{ext_color};font-weight:bold;margin-left:2px;">{ext_arrow}{abs(ext["change_pct"]):.2f}%</span>'
                    '</div>'
                )

        chips.append(
            '<div style="display:inline-block;background-color:#111111;padding:8px 12px;border-radius:6px;white-space:nowrap;vertical-align:top;">'
            f'<div style="font-size:12px;font-weight:700;color:#ffffff;">{display_name}</div>'
            f'<div style="font-size:10px;color:#888888;margin-bottom:4px;">{ticker}</div>'
            f'<div style="font-size:15px;font-weight:800;color:#ffffff;">{price_html}</div>'
            f'<div style="margin-top:2px;">{change_html}</div>'
            f'{ext_html}'
            '</div>'
        )

    st.markdown(
        f"""<div style="display:flex; flex-wrap:wrap; gap:8px;">{''.join(chips)}</div>""",
        unsafe_allow_html=True
    )


@st.dialog("관심종목 추가")
def add_watchlist_dialog():
    query = st.text_input("종목명 또는 티커 검색", placeholder="예: 삼성전자, AAPL, 엔비디아", key="watchlist_search_input")

    selected_symbol = None
    selected_name = None

    if query:
        results = search_stock(query)
        if results:
            option_labels = [f"{r['name']} ({r['symbol']})" for r in results]
            picked = st.selectbox("검색결과에서 선택", option_labels)
            picked_idx = option_labels.index(picked)
            selected_symbol = results[picked_idx]["symbol"]
            selected_name = results[picked_idx]["name"]
        else:
            st.info("검색 결과가 없습니다. 정확한 티커(예: AAPL, 005930.KS)를 입력해보세요.")
            manual_ticker = st.text_input("직접 티커 입력 (검색 결과가 없을 때)")
            if manual_ticker:
                selected_symbol = manual_ticker.strip().upper()
                selected_name = selected_symbol
                if selected_symbol.endswith(".KS") or selected_symbol.endswith(".KQ"):
                    kr_name = get_korean_name(selected_symbol)
                    if kr_name:
                        selected_name = kr_name

    if st.button("추가하기", use_container_width=True, type="primary"):
        if not selected_symbol:
            st.warning("종목을 검색해서 선택해주세요.")
        elif any(w["ticker"] == selected_symbol for w in st.session_state.watchlist):
            st.warning("이미 관심종목에 있습니다.")
        else:
            st.session_state.watchlist.append({"ticker": selected_symbol, "name": selected_name})
            save_watchlist()
            st.rerun()


@st.fragment(run_every=60)
def render_my_watchlist():
    """사용자가 직접 추가/삭제하는, 보유 여부와 무관한 관심종목 리스트"""
    is_shown = st.session_state.section_expanded.get("watchlist", True)

    title_cols = st.columns([1.6, 0.35, 0.35, 0.35, 5.15])
    with title_cols[0]:
        st.markdown(f"<div style='font-size:16px; font-weight:700; padding-top:5px; white-space:nowrap;'>⭐ 관심종목 ({len(st.session_state.watchlist)}개)</div>", unsafe_allow_html=True)
    with title_cols[1]:
        toggle_icon = "▲" if is_shown else "▼"
        if st.button(toggle_icon, key="toggle_watchlist_btn", help="펼치기/접기", use_container_width=True):
            st.session_state.section_expanded["watchlist"] = not is_shown
            st.rerun()
    with title_cols[2]:
        if st.button("➕", key="add_watchlist_btn", help="종목 추가", use_container_width=True):
            add_watchlist_dialog()
    with title_cols[3]:
        if st.session_state.watchlist:
            if st.button("🗑️", key="manage_watchlist_btn", help="관리(삭제)", use_container_width=True):
                st.session_state["show_watchlist_manage"] = not st.session_state.get("show_watchlist_manage", False)

    if not is_shown:
        return

    if not st.session_state.watchlist:
        st.caption("아직 추가된 관심종목이 없습니다.")
    else:
        items = [(w["ticker"], w["name"]) for w in st.session_state.watchlist]
        render_ticker_cards(items)

        if st.session_state.get("show_watchlist_manage"):
            st.markdown("<div style='margin-top:6px;'></div>", unsafe_allow_html=True)
            checked_tickers = []
            chk_cols = st.columns(4)
            for idx, w in enumerate(st.session_state.watchlist):
                with chk_cols[idx % 4]:
                    checked = st.checkbox(
                        f"{w['name']} ({w['ticker']})",
                        key=f"watchlist_chk_{idx}_{w['ticker']}"
                    )
                    if checked:
                        checked_tickers.append(w["ticker"])

            if st.button("선택 삭제", key="watchlist_remove_confirm", disabled=not checked_tickers):
                st.session_state.watchlist = [w for w in st.session_state.watchlist if w["ticker"] not in checked_tickers]
                for t in checked_tickers:
                    st.session_state.pop(f"watchlist_chk_{t}", None)
                save_watchlist()
                st.rerun()


@st.fragment(run_every=60)
def render_holdings_board():
    """모든 포트폴리오에 실제로 담긴 종목을 중복 없이 모아 보여주는 보유종목 보드"""
    seen = {}
    for holdings in st.session_state.portfolios.values():
        for h in holdings:
            if h["ticker"] not in seen:
                seen[h["ticker"]] = h["name"]

    if not seen:
        return

    is_shown = st.session_state.section_expanded.get("holdings", True)

    title_cols = st.columns([3.6, 0.55, 4.85])
    with title_cols[0]:
        st.markdown(f"<div style='font-size:16px; font-weight:700; padding-top:5px;'>📌 보유종목 ({len(seen)}개)</div>", unsafe_allow_html=True)
    with title_cols[1]:
        toggle_icon = "▲" if is_shown else "▼"
        if st.button(toggle_icon, key="toggle_holdings_btn", help="펼치기/접기", use_container_width=True):
            st.session_state.section_expanded["holdings"] = not is_shown
            st.rerun()

    if is_shown:
        render_ticker_cards(list(seen.items()))


def render_portfolio_table(portfolio_name, rows, total_eval_amount):
    if not rows:
        st.caption("아직 추가된 종목이 없습니다. 위의 종목 추가 버튼을 눌러보세요.")
        return

    col_widths = [1.2, 0.5, 1.3, 1.3, 0.6, 1.4, 1.4, 1.4, 0.7, 0.7, 0.7, 1.0, 1.3, 0.4]
    header_cols = st.columns(col_widths)
    header_labels = ["종목", "수량", "평단가", "현재가", "RSI", "매수금액", "평가금액", "평가손익", "수익률",
                      "목표비중", "현재비중", "신호", "조정(매수/판매)", "수정"]
    for c, label in zip(header_cols, header_labels):
        c.markdown(f"<span style='font-size:11px;color:#aaaaaa;font-weight:bold;'>{label}</span>", unsafe_allow_html=True)

    for i, r in enumerate(rows):
        row_cols = st.columns(col_widths)
        row_display_name = get_display_name(r['ticker'], r['name'])
        row_cols[0].markdown(
            f'<div style="line-height:1.3;">'
            f'<span style="font-size:12px;font-weight:700;color:#ffffff;">{row_display_name}</span><br>'
            f'<span style="font-size:10px;color:#888888;">{r["ticker"]}</span>'
            f'</div>',
            unsafe_allow_html=True
        )
        row_cols[1].write(f"{r['qty']:,.0f}")

        is_usd = not (r["ticker"].endswith(".KS") or r["ticker"].endswith(".KQ"))

        # 달러 종목은 원/달러 환율을 곱해 원화환산 값을 같이 표시
        cur_fx = get_usd_krw_rate() if is_usd else None
        buy_fx = (r.get("buy_fx_rate", 0.0) or 0.0) if is_usd else None
        if is_usd and not buy_fx:
            buy_fx = cur_fx  # 매수시 환율 미입력 시 현재 환율로 근사

        def nowrap(txt):
            """줄바꿈 없이 한 줄로 붙어 보이게 함 (수량↔평단가 사이 여백이 있어도 '원'이 다음 줄로 안 내려가도록)"""
            return f'<div style="white-space:nowrap;">{txt}</div>'

        if is_usd and cur_fx:
            row_cols[2].markdown(nowrap(combine_currency(fmt_money(r['avg_price'], is_usd, decimals=2), f"{r['avg_price'] * buy_fx:,.0f}원")), unsafe_allow_html=True)
        else:
            row_cols[2].markdown(nowrap(fmt_money(r['avg_price'], is_usd, decimals=2)), unsafe_allow_html=True)

        # RSI 표시 (과매수/과매도 색상 표시)
        if r["rsi"] is not None:
            rsi_v = r["rsi"]
            if rsi_v >= 70:
                rsi_color, rsi_tag = "#ff4d4d", "과매수"
            elif rsi_v <= 30:
                rsi_color, rsi_tag = "#4d94ff", "과매도"
            else:
                rsi_color, rsi_tag = "#ffffff", ""
            rsi_html = f"<span style='color:{rsi_color};font-weight:bold;font-size:14px;'>{rsi_v:.1f}</span>"
            if rsi_tag:
                rsi_html += f" <span style='font-size:11px;color:{rsi_color};'>({rsi_tag})</span>"
        else:
            rsi_html = "<span style='font-size:11px;color:#666;'>⏳</span>"

        target_weight = r.get("target_weight", 0.0) or 0.0

        if r["eval_amount"] is not None:
            profit_amount = r["eval_amount"] - r["buy_amount"]
            profit_pct = (profit_amount / r["buy_amount"]) * 100 if r["buy_amount"] > 0 else 0.0
            color = "#ff4d4d" if profit_pct >= 0 else "#4d94ff"
            arrow = "▲" if profit_pct >= 0 else "▼"
            current_weight = (r["eval_amount"] / total_eval_amount * 100) if total_eval_amount > 0 else 0.0
            target_amount = (target_weight / 100) * total_eval_amount
            shortfall = target_amount - r["eval_amount"]

            row_cols[3].markdown(
                nowrap(combine_currency(fmt_money(r['current_price'], is_usd, decimals=2), f"{r['current_price'] * cur_fx:,.0f}원")) if (is_usd and cur_fx)
                else nowrap(fmt_money(r['current_price'], is_usd, decimals=2)),
                unsafe_allow_html=True
            )
            row_cols[4].markdown(rsi_html, unsafe_allow_html=True)
            row_cols[5].markdown(
                nowrap(combine_currency(fmt_money(r['buy_amount'], is_usd), f"{r['buy_amount'] * buy_fx:,.0f}원")) if (is_usd and cur_fx)
                else nowrap(fmt_money(r['buy_amount'], is_usd)),
                unsafe_allow_html=True
            )
            row_cols[6].markdown(
                nowrap(combine_currency(fmt_money(r['eval_amount'], is_usd), f"{r['eval_amount'] * cur_fx:,.0f}원")) if (is_usd and cur_fx)
                else nowrap(fmt_money(r['eval_amount'], is_usd)),
                unsafe_allow_html=True
            )
            if is_usd and cur_fx:
                profit_krw = (r['eval_amount'] * cur_fx) - (r['buy_amount'] * buy_fx)
                row_cols[7].markdown(
                    nowrap(f"<span style='color:{color};font-weight:bold;'>{arrow} {fmt_money(abs(profit_amount), is_usd)} <span style='font-size:0.9em;'>({abs(profit_krw):,.0f}원)</span></span>"),
                    unsafe_allow_html=True
                )
            else:
                row_cols[7].markdown(
                    nowrap(f"<span style='color:{color};font-weight:bold;'>{arrow} {fmt_money(abs(profit_amount), is_usd)}</span>"),
                    unsafe_allow_html=True
                )
            row_cols[8].markdown(
                f"<span style='color:{color};font-weight:bold;'>{arrow} {abs(profit_pct):.1f}%</span>",
                unsafe_allow_html=True
            )
            row_cols[9].write(f"{target_weight:.0f}%")
            row_cols[10].write(f"{current_weight:.0f}%")

            # 신호: 현재비중이 목표비중 대비 초과/부족인지 표시 (±2%p는 적정으로 간주)
            diff_pct = current_weight - target_weight
            if abs(diff_pct) <= 2:
                signal_color, signal_tag = "#aaaaaa", "적정"
            elif diff_pct > 0:
                signal_color, signal_tag = "#4d94ff", "초과"
            else:
                signal_color, signal_tag = "#ff4d4d", "부족"
            row_cols[11].markdown(
                f"<div style='white-space:nowrap;'>"
                f"<span style='color:{signal_color};font-weight:bold;font-size:14px;'>{signal_tag}</span> "
                f"<span style='font-size:12px;color:{signal_color};'>({diff_pct:+.1f}%p)</span>"
                f"</div>",
                unsafe_allow_html=True
            )

            if shortfall > 0:
                shortfall_txt = f"매수 {fmt_money(shortfall, is_usd)}" + (f" ({shortfall * cur_fx:,.0f}원)" if (is_usd and cur_fx) else "")
                row_cols[12].markdown(nowrap(f"<span style='color:#ff4d4d;'>{shortfall_txt}</span>"), unsafe_allow_html=True)
            elif shortfall < 0:
                shortfall_txt = f"판매 {fmt_money(abs(shortfall), is_usd)}" + (f" ({abs(shortfall) * cur_fx:,.0f}원)" if (is_usd and cur_fx) else "")
                row_cols[12].markdown(nowrap(f"<span style='color:#4d94ff;'>{shortfall_txt}</span>"), unsafe_allow_html=True)
            else:
                row_cols[12].write("-")
        else:
            row_cols[3].write("⏳")
            row_cols[4].markdown(rsi_html, unsafe_allow_html=True)
            row_cols[5].markdown(fmt_money(r['buy_amount'], is_usd), unsafe_allow_html=True)
            row_cols[6].write("⏳")
            row_cols[7].write("-")
            row_cols[8].write("-")
            row_cols[9].write(f"{target_weight:.0f}%")
            row_cols[10].write("-")
            row_cols[11].write("-")
            row_cols[12].write("-")


        if row_cols[13].button("✏️", key=f"edit_{portfolio_name}_{i}", help="수량/평단가/목표비중 수정 및 삭제"):
            edit_stock_dialog(portfolio_name, i)


def build_weight_donut_svg(rows, total_eval_amount, size=150):
    """종목별 현재 비중 도넛 SVG를 문자열로 반환. 각 조각 안(중간 반지름 위치)에 비중% 라벨을 표시.
    가운데 구멍에는 종목 수를 표시. 요약 옆에 배치할 수 있도록 SVG만 돌려줌."""
    items = []
    for r in rows:
        if r["eval_amount"] and total_eval_amount > 0:
            w = r["eval_amount"] / total_eval_amount * 100
            items.append((get_display_name(r["ticker"], r["name"]), w))
    if not items:
        return ""
    items.sort(key=lambda x: -x[1])

    palette = ["#4dd2ff", "#ff9f4d", "#4dff88", "#ff4d4d", "#c04dff", "#ffd633",
               "#4d94ff", "#ff4dcb", "#9fe14d", "#4dffea", "#ff6f4d", "#8888ff"]

    r_out, r_in = size / 2 - 4, size / 2 - 30
    r_mid = (r_out + r_in) / 2
    cx = cy = size / 2
    segments = ""
    labels = ""
    angle = -90.0
    for i, (name, w) in enumerate(items):
        color = palette[i % len(palette)]
        sweep = w / 100 * 360
        a0 = math.radians(angle)
        a1 = math.radians(angle + sweep)
        x0o, y0o = cx + r_out * math.cos(a0), cy + r_out * math.sin(a0)
        x1o, y1o = cx + r_out * math.cos(a1), cy + r_out * math.sin(a1)
        x0i, y0i = cx + r_in * math.cos(a1), cy + r_in * math.sin(a1)
        x1i, y1i = cx + r_in * math.cos(a0), cy + r_in * math.sin(a0)
        large = 1 if sweep > 180 else 0
        segments += (
            f'<path d="M {x0o:.2f} {y0o:.2f} A {r_out} {r_out} 0 {large} 1 {x1o:.2f} {y1o:.2f} '
            f'L {x0i:.2f} {y0i:.2f} A {r_in} {r_in} 0 {large} 0 {x1i:.2f} {y1i:.2f} Z" fill="{color}" />'
        )
        # 조각이 충분히 클 때만(8% 이상) 안에 % 라벨 표시
        if w >= 8:
            mid_ang = math.radians(angle + sweep / 2)
            lx = cx + r_mid * math.cos(mid_ang)
            ly = cy + r_mid * math.sin(mid_ang)
            labels += f'<text x="{lx:.1f}" y="{ly+3:.1f}" text-anchor="middle" font-size="11" font-weight="800" fill="#0a0a0a">{w:.0f}%</text>'
        angle += sweep

    center = f'<text x="{cx}" y="{cy-2}" text-anchor="middle" font-size="12" font-weight="700" fill="#ccc">{len(items)}종목</text>'
    return f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">{segments}{labels}{center}</svg>'


def build_weight_legend(rows, total_eval_amount):
    """도넛 아래에 넣을 범례 HTML (종목 · 현재% · 목표%)."""
    items = []
    for r in rows:
        if r["eval_amount"] and total_eval_amount > 0:
            w = r["eval_amount"] / total_eval_amount * 100
            tw = r.get("target_weight", 0.0) or 0.0
            items.append((get_display_name(r["ticker"], r["name"]), w, tw))
    if not items:
        return ""
    items.sort(key=lambda x: -x[1])
    palette = ["#4dd2ff", "#ff9f4d", "#4dff88", "#ff4d4d", "#c04dff", "#ffd633",
               "#4d94ff", "#ff4dcb", "#9fe14d", "#4dffea", "#ff6f4d", "#8888ff"]
    legend = (
        '<div style="display:flex;align-items:center;gap:6px;margin:2px 0;font-size:10px;color:#777;">'
        '<span style="width:10px;flex:0 0 auto;"></span>'
        '<span style="flex:1 1 auto;min-width:0;">종목</span>'
        '<span style="flex:0 0 auto;width:40px;text-align:right;">현재</span>'
        '<span style="flex:0 0 auto;width:40px;text-align:right;">목표</span>'
        '</div>'
    )
    for i, (name, w, tw) in enumerate(items):
        color = palette[i % len(palette)]
        legend += (
            '<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">'
            f'<span style="width:10px;height:10px;border-radius:2px;background:{color};display:inline-block;flex:0 0 auto;"></span>'
            f'<span style="font-size:12px;color:#ddd;flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{name}</span>'
            f'<span style="font-size:12px;color:#fff;font-weight:700;flex:0 0 auto;width:40px;text-align:right;">{w:.1f}%</span>'
            f'<span style="font-size:12px;color:#888;flex:0 0 auto;width:40px;text-align:right;">{tw:.0f}%</span>'
            '</div>'
        )
    return legend


def render_portfolio_cards_mobile(portfolio_name, rows, total_eval_amount):
    """모바일 화면용 보유종목 카드뷰 (더리치 스타일).
    상단: 종목명 ↔ 수량 / 왼쪽열: 평가금액·수익·비중 / 오른쪽열: 평단가·현재가·신호.
    RSI만 제외하고 나머지 정보를 왼/오 2열로 나눠 보여줌."""
    if not rows:
        st.caption("아직 추가된 종목이 없습니다. 위의 종목 추가 버튼을 눌러보세요.")
        return

    def kv(label, value_html):
        return (
            '<div style="margin-top:6px;line-height:1.3;">'
            f'<div style="font-size:11px;color:#888;">{label}</div>'
            f'<div style="font-size:14px;font-weight:700;">{value_html}</div>'
            '</div>'
        )

    for i, r in enumerate(rows):
        is_usd = not (r["ticker"].endswith(".KS") or r["ticker"].endswith(".KQ"))
        cur_fx = get_usd_krw_rate() if is_usd else None
        buy_fx = (r.get("buy_fx_rate", 0.0) or 0.0) if is_usd else None
        if is_usd and not buy_fx:
            buy_fx = cur_fx
        display_name = get_display_name(r["ticker"], r["name"])
        target_weight = r.get("target_weight", 0.0) or 0.0
        _show_krw = st.session_state.get("currency_mode_radio", "$ 달러") == "₩ 원화"

        avg_txt = combine_currency(fmt_money(r['avg_price'], is_usd, decimals=2), f"{r['avg_price'] * buy_fx:,.0f}원") if (is_usd and cur_fx) else fmt_money(r['avg_price'], is_usd, decimals=2)

        # RSI + 신호 (30이하 매수 / 70이상 매도)
        rsi_v = r.get("rsi")
        if rsi_v is not None:
            if rsi_v <= 30:
                rsi_html = f'<span style="color:#4d94ff;font-weight:700;">{rsi_v:.0f}</span> <span style="color:#4d94ff;font-size:11px;">매수</span>'
            elif rsi_v >= 70:
                rsi_html = f'<span style="color:#ff4d4d;font-weight:700;">{rsi_v:.0f}</span> <span style="color:#ff4d4d;font-size:11px;">매도</span>'
            else:
                rsi_html = f'<span style="color:#ddd;font-weight:700;">{rsi_v:.0f}</span> <span style="color:#888;font-size:11px;">중립</span>'
        else:
            rsi_html = '<span style="color:#666;">-</span>'

        # 52주 신고가 대비 하락률
        high_52 = r.get("high_52w")
        if high_52 and r.get("current_price"):
            drop_from_high = (r["current_price"] - high_52) / high_52 * 100
            drop_html = f'<span style="color:#4d94ff;font-weight:700;">{drop_from_high:.1f}%</span>'
        else:
            drop_html = '<span style="color:#666;">-</span>'

        if r["eval_amount"] is not None:
            profit_amount = r["eval_amount"] - r["buy_amount"]
            profit_pct = (profit_amount / r["buy_amount"]) * 100 if r["buy_amount"] > 0 else 0.0
            color = "#ff4d4d" if profit_pct >= 0 else "#4d94ff"
            arrow = "▲" if profit_pct >= 0 else "▼"
            current_weight = (r["eval_amount"] / total_eval_amount * 100) if total_eval_amount > 0 else 0.0

            cur_txt = combine_currency(fmt_money(r['current_price'], is_usd, decimals=2), f"{r['current_price'] * cur_fx:,.0f}원") if (is_usd and cur_fx) else fmt_money(r['current_price'], is_usd, decimals=2)

            # 수익금 (통화 토글)
            if is_usd and cur_fx and _show_krw:
                profit_money = f'{arrow} {abs(profit_amount * cur_fx):,.0f}원'
            else:
                profit_money = f'{arrow} {fmt_money(abs(profit_amount), is_usd)}'
            profit_money_html = f'<span style="color:{color};">{profit_money}</span>'
            profit_pct_html = f'<span style="color:{color};">{arrow} {abs(profit_pct):.1f}%</span>'

            diff_pct = current_weight - target_weight
            target_amount = (target_weight / 100) * total_eval_amount
            shortfall = target_amount - r["eval_amount"]

            def _adj_txt(amount, color_hex, word):
                if is_usd and cur_fx and _show_krw:
                    base = f'{word} {amount * cur_fx:,.0f}원'
                else:
                    base = f'{word} {fmt_money(amount, is_usd)}'
                return f'<span style="color:{color_hex};">{base}</span>'

            if shortfall > 0:
                adjust_val = _adj_txt(shortfall, "#ff4d4d", "매수")
            elif shortfall < 0:
                adjust_val = _adj_txt(abs(shortfall), "#4d94ff", "판매")
            else:
                adjust_val = '<span style="color:#aaa;">균형</span>'

            weight_html = f'{current_weight:.0f}% <span style="color:#888;font-size:12px;">/ 목표 {target_weight:.0f}%</span>'

            # 매수금 / 평가금 (통화 토글 반영)
            if is_usd and cur_fx and _show_krw:
                buy_amt_txt = f'{r["buy_amount"] * (buy_fx or cur_fx):,.0f}원'
                eval_amt_txt = f'{r["eval_amount"] * cur_fx:,.0f}원'
            else:
                buy_amt_txt = fmt_money(r["buy_amount"], is_usd)
                eval_amt_txt = fmt_money(r["eval_amount"], is_usd)

            left_col = (
                kv("RSI (신호)", rsi_html)
                + kv("현재가", cur_txt)
                + kv("매수금", buy_amt_txt)
                + kv("수익금", profit_money_html)
                + kv("비중 / 목표", weight_html)
            )
            right_col = (
                kv("52주고점대비", drop_html)
                + kv("평단가", avg_txt)
                + kv("평가금", eval_amt_txt)
                + kv("손익률", profit_pct_html)
                + kv("조정필요", adjust_val)
            )
        else:
            left_col = kv("RSI (신호)", rsi_html) + kv("현재가", "⏳") + kv("평단가", avg_txt)
            right_col = kv("52주고점대비", drop_html) + kv("비중", f'목표 {target_weight:.0f}%')

        st.markdown(
            (
                '<div style="background-color:#161616;border-radius:8px;padding:14px 16px;margin-bottom:2px;width:100%;box-sizing:border-box;">'
                '<div style="display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid #262626;padding-bottom:8px;margin-bottom:2px;">'
                f'<span style="font-size:16px;font-weight:800;color:#ffffff;">{display_name} <span style="font-size:11px;color:#888;font-weight:400;">({r["ticker"]})</span></span>'
                f'<span style="font-size:12px;color:#999;">수량&nbsp;&nbsp;<span style="color:#fff;font-weight:700;font-size:14px;">{r["qty"]:,.0f}</span></span>'
                '</div>'
                '<div style="display:flex;gap:16px;">'
                f'<div style="flex:1;min-width:0;">{left_col}</div>'
                f'<div style="flex:1;min-width:0;">{right_col}</div>'
                '</div>'
                '</div>'
            ),
            unsafe_allow_html=True
        )
        bcols = st.columns([1, 1])
        with bcols[0]:
            if st.button("추가매수", key=f"addmore_mobile_{portfolio_name}_{i}", use_container_width=True):
                add_more_dialog(portfolio_name, i)
        with bcols[1]:
            if st.button("수정 · 삭제", key=f"edit_mobile_{portfolio_name}_{i}", use_container_width=True):
                edit_stock_dialog(portfolio_name, i)
        st.markdown("<div style='margin-bottom:10px;'></div>", unsafe_allow_html=True)


st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)

title_cols = st.columns([3, 1])
with title_cols[0]:
    st.markdown(
        "<h3 style='margin:0px;padding:6px 0;font-weight:800;letter-spacing:-0.5px;font-size:22px;'>"
        "<span style='display:inline-block;width:5px;height:22px;background:linear-gradient(180deg,#4dd2ff,#4d94ff);border-radius:2px;margin-right:10px;vertical-align:-3px;'></span>"
        "내 포트폴리오</h3>",
        unsafe_allow_html=True
    )
with title_cols[1]:
    if st.button("＋ 생성", use_container_width=True):
        create_portfolio_dialog()

if not st.session_state.portfolios:
    st.info("아직 만든 포트폴리오가 없습니다. 오른쪽 위 버튼으로 새 포트폴리오를 만들어보세요.")
else:
    portfolio_names = list(st.session_state.portfolios.keys())

    # 계좌 선택 상태 기본값 초기화 (한 번만) — 체크박스가 아래에서 그려지기 전에
    # 총합산이 위에서 계산되므로, 세션에 기본값을 먼저 넣어둠
    for _pn in portfolio_names:
        if f"sel_{_pn}" not in st.session_state:
            st.session_state[f"sel_{_pn}"] = True

    view_mode = st.radio(
        "보기 방식", ["자동 (기기에 맞춤)", "카드형", "테이블형"], horizontal=True,
        key="view_mode_radio", label_visibility="collapsed"
    )

    # ===== 1단계: 전 계좌 계산 (총합산 먼저 그리기 위해) =====
    acct_data = {}   # p_name -> dict(rows, totals, fx, ...)
    grand_buy_krw = 0.0
    grand_eval_krw = 0.0
    grand_has_any = False
    grand_holdings = []

    for p_name in portfolio_names:
        holdings = st.session_state.portfolios[p_name]
        rows, total_buy_amount, total_eval_amount, fx_summary = compute_portfolio_rows(holdings)
        port_is_usd = any(not (h["ticker"].endswith(".KS") or h["ticker"].endswith(".KQ")) for h in holdings)
        has_fx = fx_summary["has_data"] and fx_summary["cur_fx"]
        cur_fx_now = fx_summary["cur_fx"] or get_usd_krw_rate() or 1350.0
        if has_fx:
            # 해외+국내 섞인 계좌도 전체 원화로 정확히 합산
            acct_buy_krw = fx_summary["total_buy_krw"]
            acct_eval_krw = fx_summary["total_eval_krw"]
        else:
            acct_buy_krw = total_buy_amount
            acct_eval_krw = total_eval_amount

        selected = st.session_state.get(f"sel_{p_name}", True)
        if selected and total_eval_amount > 0:
            grand_buy_krw += acct_buy_krw
            grand_eval_krw += acct_eval_krw
            grand_has_any = True
            for _r in rows:
                if _r.get("eval_amount"):
                    _is_usd = not (_r["ticker"].endswith(".KS") or _r["ticker"].endswith(".KQ"))
                    _ev_krw = _r["eval_amount"] * cur_fx_now if _is_usd else _r["eval_amount"]
                    _nm = get_display_name(_r["ticker"], _r["name"])
                    grand_holdings.append((_nm, _ev_krw))

        acct_data[p_name] = dict(
            holdings=holdings, rows=rows, total_buy=total_buy_amount, total_eval=total_eval_amount,
            fx=fx_summary, port_is_usd=port_is_usd, has_fx=has_fx,
            acct_buy_krw=acct_buy_krw, acct_eval_krw=acct_eval_krw,
        )

    # ===== 2단계: 총합산 카드 (4개 숫자 항상 표시) + 도넛(펼치기) =====
    if grand_has_any:
        g_profit = grand_eval_krw - grand_buy_krw
        g_pct = (g_profit / grand_buy_krw * 100) if grand_buy_krw else 0
        g_color = "#ff4d4d" if g_pct >= 0 else "#4d94ff"
        g_arrow = "▲" if g_pct >= 0 else "▼"

        st.markdown(
            '<div style="background:linear-gradient(135deg,#151d2a,#0f1620);border:1px solid #2a3a52;border-radius:12px;padding:16px 18px;margin-bottom:10px;">'
            '<div style="font-size:14px;font-weight:800;color:#4dd2ff;margin-bottom:10px;">선택 계좌 총 합산 (원화 기준)</div>'
            '<div style="display:flex;gap:14px 24px;flex-wrap:wrap;">'
            f'<div style="flex:1 1 120px;"><div style="font-size:11px;color:#888;">총 매수금액</div><div style="font-size:17px;font-weight:800;color:#fff;">{grand_buy_krw:,.0f}원</div></div>'
            f'<div style="flex:1 1 120px;"><div style="font-size:11px;color:#888;">총 평가금액</div><div style="font-size:19px;font-weight:800;color:#fff;">{grand_eval_krw:,.0f}원</div></div>'
            f'<div style="flex:1 1 120px;"><div style="font-size:11px;color:#888;">총 손익</div><div style="font-size:17px;font-weight:800;color:{g_color};">{g_arrow} {abs(g_profit):,.0f}원</div></div>'
            f'<div style="flex:1 1 120px;"><div style="font-size:11px;color:#888;">총 손익률</div><div style="font-size:17px;font-weight:800;color:{g_color};">{g_arrow} {abs(g_pct):.1f}%</div></div>'
            '</div></div>',
            unsafe_allow_html=True
        )

        # 종목별 비중 도넛 (펼치면 나옴)
        merged = {}
        for nm, ev in grand_holdings:
            merged[nm] = merged.get(nm, 0.0) + ev
        holdings_list = sorted(merged.items(), key=lambda x: -x[1])
        if grand_eval_krw > 0 and holdings_list:
            with st.expander("종목별 비중 보기 (전체 합산)"):
                palette = ["#4dd2ff", "#ff9f4d", "#4dff88", "#ff4d4d", "#c04dff", "#ffd633",
                           "#4d94ff", "#ff4dcb", "#9fe14d", "#4dffea", "#ff6f4d", "#8888ff"]
                _size = 170
                _ro, _ri = _size/2 - 4, _size/2 - 34
                _rm = (_ro + _ri) / 2
                _cx = _cy = _size / 2
                segs = labs = leg = ""
                ang = -90.0
                _tot = sum(v for _, v in holdings_list)
                for i, (an, av) in enumerate(holdings_list):
                    col = palette[i % len(palette)]
                    w = av / _tot * 100
                    sw = w / 100 * 360
                    a0, a1 = math.radians(ang), math.radians(ang + sw)
                    x0o, y0o = _cx + _ro*math.cos(a0), _cy + _ro*math.sin(a0)
                    x1o, y1o = _cx + _ro*math.cos(a1), _cy + _ro*math.sin(a1)
                    x0i, y0i = _cx + _ri*math.cos(a1), _cy + _ri*math.sin(a1)
                    x1i, y1i = _cx + _ri*math.cos(a0), _cy + _ri*math.sin(a0)
                    lg = 1 if sw > 180 else 0
                    segs += f'<path d="M {x0o:.1f} {y0o:.1f} A {_ro} {_ro} 0 {lg} 1 {x1o:.1f} {y1o:.1f} L {x0i:.1f} {y0i:.1f} A {_ri} {_ri} 0 {lg} 0 {x1i:.1f} {y1i:.1f} Z" fill="{col}"/>'
                    if w >= 7:
                        ma = math.radians(ang + sw/2)
                        lx, ly = _cx + _rm*math.cos(ma), _cy + _rm*math.sin(ma)
                        labs += f'<text x="{lx:.1f}" y="{ly+3:.1f}" text-anchor="middle" font-size="11" font-weight="800" fill="#0a0a0a">{w:.0f}%</text>'
                    leg += (
                        '<div style="display:flex;align-items:center;gap:6px;margin:3px 0;">'
                        f'<span style="width:11px;height:11px;border-radius:3px;background:{col};flex:0 0 auto;"></span>'
                        f'<span style="font-size:13px;color:#ddd;flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{an}</span>'
                        f'<span style="font-size:13px;color:#fff;font-weight:700;flex:0 0 auto;">{w:.1f}%</span>'
                        '</div>'
                    )
                    ang += sw
                st.markdown(
                    '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;">'
                    f'<div style="flex:0 0 auto;"><svg width="{_size}" height="{_size}" viewBox="0 0 {_size} {_size}">{segs}{labs}'
                    f'<text x="{_cx}" y="{_cy-4}" text-anchor="middle" font-size="12" font-weight="700" fill="#888">종목</text>'
                    f'<text x="{_cx}" y="{_cy+12}" text-anchor="middle" font-size="12" font-weight="700" fill="#888">비중</text></svg></div>'
                    f'<div style="flex:1 1 200px;min-width:200px;">{leg}</div>'
                    '</div>',
                    unsafe_allow_html=True
                )
    else:
        st.info("아래에서 계좌를 선택하면 총 합산이 여기 표시됩니다.")

    st.markdown("<div style='font-size:13px;color:#888;margin:12px 0 6px;'>계좌 목록</div>", unsafe_allow_html=True)

    # ===== 3단계: 계좌별 요약 행 + 펼치면 상세 =====
    for p_idx, p_name in enumerate(portfolio_names):
        d = acct_data[p_name]
        holdings, rows = d["holdings"], d["rows"]
        total_buy_amount, total_eval_amount = d["total_buy"], d["total_eval"]
        fx_summary, port_is_usd, has_fx = d["fx"], d["port_is_usd"], d["has_fx"]

        # 요약 한 줄 계산
        if total_buy_amount > 0:
            tp = total_eval_amount - total_buy_amount
            tpp = (tp / total_buy_amount) * 100
            c = "#ff4d4d" if tpp >= 0 else "#4d94ff"
            a = "▲" if tpp >= 0 else "▼"
            eval_disp = f"{d['acct_eval_krw']:,.0f}원"
            summary_line = f'<span style="color:#fff;font-weight:700;">{eval_disp}</span> <span style="color:{c};font-weight:700;">{a} {abs(tpp):.1f}%</span>'
        else:
            summary_line = '<span style="color:#666;">종목 없음</span>'

        head_cols = st.columns([0.8, 3.2, 1])
        with head_cols[0]:
            st.checkbox("합산", key=f"sel_{p_name}")
        with head_cols[1]:
            st.markdown(
                f'<div style="padding-top:4px;"><span style="font-size:15px;font-weight:800;color:#fff;">{p_name}</span> '
                f'<span style="font-size:12px;color:#888;">({len(holdings)}종목)</span><br>{summary_line}</div>',
                unsafe_allow_html=True
            )
        with head_cols[2]:
            expanded_acct = st.toggle("상세보기", value=False, key=f"exp_{p_name}")

        if expanded_acct:
            acct_has_usd = any(not (h["ticker"].endswith(".KS") or h["ticker"].endswith(".KQ")) for h in holdings)
            if acct_has_usd:
                cmode = st.radio("통화", ["$ 달러", "₩ 원화"], horizontal=True,
                                 key=f"currency_mode_radio_{p_name}", label_visibility="collapsed")
                show_krw = (cmode == "₩ 원화")
            else:
                show_krw = False
            st.session_state["currency_mode_radio"] = "₩ 원화" if show_krw else "$ 달러"

            # 상세 요약 4칸
            if total_buy_amount > 0:
                total_profit = total_eval_amount - total_buy_amount
                total_profit_pct = (total_profit / total_buy_amount) * 100
                color = "#ff4d4d" if total_profit_pct >= 0 else "#4d94ff"
                arrow = "▲" if total_profit_pct >= 0 else "▼"

                def metric(label, value_html):
                    return (
                        '<div style="min-width:0;">'
                        f'<div style="font-size:11px;color:#888888;">{label}</div>'
                        f'<div style="font-size:15px;font-weight:700;color:#ffffff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{value_html}</div>'
                        '</div>'
                    )

                buy_txt = combine_currency(fmt_money(total_buy_amount, port_is_usd), f"{fx_summary['buy_krw']:,.0f}원") if has_fx else fmt_money(total_buy_amount, port_is_usd)
                eval_txt = combine_currency(fmt_money(total_eval_amount, port_is_usd), f"{fx_summary['eval_krw']:,.0f}원") if has_fx else fmt_money(total_eval_amount, port_is_usd)
                if has_fx and show_krw:
                    stock_gain_krw = total_profit * fx_summary["cur_fx"]
                    profit_txt = f'<span style="color:{color};">{arrow} {abs(stock_gain_krw):,.0f}원</span>'
                else:
                    profit_txt = f'<span style="color:{color};">{arrow} {fmt_money(abs(total_profit), port_is_usd)}</span>'

                grid = (
                    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 14px;">'
                    + metric("총 매수금액", buy_txt)
                    + metric("총 평가금액", eval_txt)
                    + metric("평가손익", profit_txt)
                    + metric("손익률", f'<span style="color:{color};">{arrow} {abs(total_profit_pct):.1f}%</span>')
                    + '</div>'
                )
                fx_line = ""
                if has_fx:
                    fx_c = "#ff4d4d" if fx_summary["fx_gain"] >= 0 else "#4d94ff"
                    fx_a = "▲" if fx_summary["fx_gain"] >= 0 else "▼"
                    fx_line = f'<div style="font-size:11px;color:#888;margin-top:6px;">환차손익 <span style="color:{fx_c};font-weight:700;">{fx_a} {abs(fx_summary["fx_gain"]):,.0f}원</span></div>'
                st.markdown(f'<div>{grid}{fx_line}</div>', unsafe_allow_html=True)

            btn_cols = st.columns([1, 1, 3])
            with btn_cols[0]:
                if st.button("종목 추가", key=f"add_{p_name}", use_container_width=True):
                    add_stock_dialog(p_name)
            with btn_cols[1]:
                if st.button("계좌 삭제", key=f"delp_{p_name}", use_container_width=True):
                    del st.session_state.portfolios[p_name]
                    save_portfolios()
                    st.rerun()

            st.markdown("<hr style='border-color:#222222; margin-top:8px; margin-bottom:8px;'>", unsafe_allow_html=True)
            if view_mode == "카드형":
                render_portfolio_cards_mobile(p_name, rows, total_eval_amount)
            elif view_mode == "테이블형":
                with st.container(key=f"pc_table_{p_idx}"):
                    render_portfolio_table(p_name, rows, total_eval_amount)
            else:
                with st.container(key=f"auto_card_{p_idx}"):
                    render_portfolio_cards_mobile(p_name, rows, total_eval_amount)
                with st.container(key=f"auto_table_{p_idx}"):
                    render_portfolio_table(p_name, rows, total_eval_amount)

            if total_eval_amount > 0:
                with st.expander("📊 종목별 비중 (도넛 차트)"):
                    donut_svg = build_weight_donut_svg(rows, total_eval_amount, size=140)
                    st.markdown(f'<div style="display:flex;justify-content:center;margin:8px 0;">{donut_svg}</div>', unsafe_allow_html=True)
                    st.markdown(build_weight_legend(rows, total_eval_amount), unsafe_allow_html=True)

        st.markdown("<div style='height:10px;border-bottom:1px solid #222;margin-bottom:10px;'></div>", unsafe_allow_html=True)
