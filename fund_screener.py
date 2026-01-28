import akshare as ak
import pandas as pd
import numpy as np
import json
import datetime
import os
import time
import socket
import requests # 新增：用于微信推送
import google.genai as genai_sdk
from google.genai import types

# ================= 配置区域 =================
# 从环境变量读取 Key，保护隐私安全
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

PORTFOLIO_FILE = 'portfolio.json'
TARGET_COUNT = 5          
START_1Y_RETURN = 8.0     
START_MAX_DD = 20.0       

if not GEMINI_API_KEY:
    print("❌ 错误：未发现 GEMINI_API_KEY。如果是本地测试，请执行 export GEMINI_API_KEY='你的Key'")
# ============================================

# 初始化 Gemini 客户端
client = genai_sdk.Client(api_key=GEMINI_API_KEY)

# --- 1. 量化筛选模块 ---
def calculate_indicators(df_history):
    if df_history is None or len(df_history) < 150: return None, None
    df_history = df_history.sort_values('净值日期')
    nav = pd.to_numeric(df_history['单位净值'], errors='coerce').ffill()
    returns = nav.pct_change().dropna()
    max_dd = abs(((nav - nav.cummax()) / nav.cummax()).min() * 100)
    rf = 0.02 / 252
    sharpe = (returns.mean() - rf) / returns.std() * np.sqrt(252)
    return sharpe, max_dd

def run_quant_screening():
    print("\n🔍 正在执行全市场量化扫描...")
    try:
        df_rank = ak.fund_open_fund_rank_em(symbol="全部")
        col = next((c for c in ['近1年', '近一年', '1年'] if c in df_rank.columns), None)
        df_rank[col] = pd.to_numeric(df_rank[col].astype(str).str.replace('%', ''), errors='coerce')
        
        curr_ret, curr_dd = START_1Y_RETURN, START_MAX_DD
        final_recommends = []
        
        for i in range(2): 
            candidates = df_rank[df_rank[col] > curr_ret].sort_values(by=col, ascending=False).head(50) # 减少扫描量加快速度
            for _, row in candidates.iterrows():
                try:
                    df_h = ak.fund_open_fund_info_em(symbol=row['基金代码'], indicator="单位净值走势")
                    df_h['净值日期'] = pd.to_datetime(df_h['净值日期'])
                    df_h = df_h[df_h['净值日期'] >= (datetime.datetime.now() - datetime.timedelta(days=365))]
                    sharpe, max_dd = calculate_indicators(df_h)
                    if sharpe and max_dd < curr_dd:
                        final_recommends.append({"Code": row['基金代码'], "Name": row['基金简称'], "Return": row[col], "Sharpe": round(sharpe, 2)})
                    if len(final_recommends) >= TARGET_COUNT: break
                except: continue
                time.sleep(0.1)
            if len(final_recommends) >= TARGET_COUNT: break
            curr_ret -= 2.0; curr_dd += 10.0
        return final_recommends
    except: return []

# --- 2. 影子账户核算模块 ---
def calculate_portfolio():
    if not os.path.exists(PORTFOLIO_FILE): return "影子账户数据文件不存在", 0
    with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    total_val = 0          # 总市值
    total_daily_p = 0      # 今日总盈亏额
    total_hold_p = 0       # 累计总盈亏额
    lines = []
    
    print(f"\n📊 正在同步持仓净值 (共 {len(data['holdings'])} 只)...")
    for item in data['holdings']:
        time.sleep(0.5) # 避免请求过快被封
        try:
            # 获取基金净值数据
            df = ak.fund_open_fund_info_em(symbol=item['code'], indicator="单位净值走势")
            nav = float(df.iloc[-1]['单位净值'])      # 最新净值
            prev_nav = float(df.iloc[-2]['单位净值']) # 前一交易日净值
            
            # 1. 计算今日单只收益
            daily_rate = (nav - prev_nav) / prev_nav
            current_val = nav * item['shares']
            daily_profit = current_val * (daily_rate / (1 + daily_rate))
            
            # 2. 计算累计持有收益
            hold_profit = (nav - item['cost_price']) * item['shares']
            hold_rate = (nav - item['cost_price']) / item['cost_price'] * 100
            
            # 累加总计
            total_val += current_val
            total_daily_p += daily_profit
            total_hold_p += hold_profit
            
            # 格式化单只基金详情
            lines.append(
                f"- {item['name']}({item['code']}): "
                f"今日 {daily_profit:+.2f}元 | "
                f"持有 {hold_profit:+.2f}元({hold_rate:+.2f}%)"
            )
        except Exception as e:
            print(f"⚠️ 无法获取 {item['code']} 数据: {e}")
            continue
            
    # 构造汇总报告
    summary = (
        f"💰 【资产汇总】\n"
        f"总市值: {total_val:.2f} 元\n"
        f"今日预估盈亏: {total_daily_p:+.2f} 元\n"
        f"累计持有盈亏: {total_hold_p:+.2f} 元\n"
        f"--------------------------\n"
        f"📈 【持仓明细】\n"
    )
    return summary + "\n".join(lines)

# --- 3. Gemini API 调用 (增强联网与重试) ---
def ask_gemini(prompt):
    print("\n🤖 正在请求 Gemini 2.0 进行联网深度分析...")
    max_retries = 3
    for i in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
            )
            return response.text
        except Exception as e:
            if "429" in str(e) or "54" in str(e):
                print(f"⚠️ 触发限制或网络波动，休眠 60 秒后重试 ({i+1}/{max_retries})...")
                time.sleep(60)
            else:
                return f"AI 诊断失败: {e}"
    return "❌ 达到最大尝试次数，AI 分析未生成。"

# --- 4. 微信推送模块 ---
def push_to_wechat(content):
    if not PUSHPLUS_TOKEN:
        print("⚠️ 未配置 PUSHPLUS_TOKEN，跳过推送。")
        return
    print("\n发送微信消息中...")
    url = "http://www.pushplus.plus/send"
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": f"💰 基金日报 {datetime.date.today()}",
        "content": content.replace("\n", "<br>"),
        "template": "html"
    }
    requests.post(url, json=data)

# --- 主程序 ---
if __name__ == "__main__":
    # 步骤 1: 核算我的资产
    portfolio_report = calculate_portfolio()
    
    # 步骤 2: 寻找机会
    recommends = run_quant_screening()
    recommends_str = "\n".join([f"- {r['Name']}({r['Code']}): 收益{r['Return']}%, 夏普{r['Sharpe']}" for r in recommends])

    # 步骤 3: 构造 Prompt
    prompt = f"你是一位顶级的量化投资专家。请结合【今日实时市场新闻】和以下数据分析：\n\n【我的持仓】\n{portfolio_report}\n\n【潜力基金】\n{recommends_str}\n\n要求分析今日盈亏原因、给出调仓建议和明日指引。"

    # 步骤 4: 获取 AI 结果
    ai_advice = ask_gemini(prompt)
    
    # 步骤 5: 汇总结果
    final_report = f"### 1. 持仓概况\n{portfolio_report}\n\n### 2. 量化优选\n{recommends_str}\n\n### 3. AI 投资建议\n{ai_advice}"
    
    # 打印到控制台
    print("\n" + "="*50 + "\n" + final_report + "\n" + "="*50)
    
    # 自动发送微信
    push_to_wechat(final_report)
