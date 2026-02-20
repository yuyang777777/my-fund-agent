import os
import time
import datetime
import json
import pandas as pd
import akshare as ak
import requests
from openai import OpenAI

# ================= 配置区域 =================
# 建议在 GitHub Secrets 中设置这些变量
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")
PORTFOLIO_FILE = "portfolio.json"

def calculate_portfolio():
    if not os.path.exists(PORTFOLIO_FILE): return "❌ 找不到持仓文件 portfolio.json", 0
    with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    total_val, total_daily_p, total_hold_p = 0, 0, 0
    lines = []
    
    print("📊 正在同步持仓净值...")
    for item in data['holdings']:
        try:
            df = ak.fund_open_fund_info_em(symbol=item['code'], indicator="单位净值走势")
            nav = float(df.iloc[-1]['单位净值'])
            prev_nav = float(df.iloc[-2]['单位净值'])
            
            daily_rate = (nav - prev_nav) / prev_nav
            current_val = nav * item['shares']
            daily_profit = current_val * (daily_rate / (1 + daily_rate))
            hold_profit = (nav - item['cost_price']) * item['shares']
            hold_rate = (nav - item['cost_price']) / item['cost_price'] * 100
            
            total_val += current_val
            total_daily_p += daily_profit
            total_hold_p += hold_profit
            lines.append(f"- {item['name']}({item['code']}): 今日 {daily_profit:+.2f}元 | 持有 {hold_profit:+.2f}元({hold_rate:+.2f}%)")
        except: continue
            
    summary = (
        f"💰 【资产汇总】\n总市值: {total_val:.2f} 元\n"
        f"今日预估盈亏: {total_daily_p:+.2f} 元\n"
        f"累计持有盈亏: {total_hold_p:+.2f} 元\n"
        f"--------------------------\n"
    )
    return summary + "\n".join(lines), total_daily_p

def get_fund_recommends():
    try:
        # 这个插件在凌晨数据最全
        df = ak.fund_open_fund_rank_em(symbol="全部")
        df = df[df['基金类型'].str.contains('股票型|混合型|指数型', na=False)]
        df['日增长率'] = pd.to_numeric(df['日增长率'], errors='coerce')
        # 取涨幅前 5
        top_funds = df.nlargest(5, '日增长率')
        
        res = ["📈 【今日市场领涨参考】"]
        for _, row in top_funds.iterrows():
            res.append(f"- {row['基金简称']}({row['基金代码']}): 涨幅 {row['日增长率']}%")
        return "\n".join(res)
    except:
        return "⚠️ 正在等待数据中心更新..."

def ask_ai(prompt):
    # DeepSeek 稳定性远超 Gemini 免费版
    if not DEEPSEEK_API_KEY: return "❌ AI 钥匙未配置"
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": "你是一位专业的量化投资顾问。"},{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ AI 分析中断: {e}"

def push_to_wechat(content):
    if not PUSHPLUS_TOKEN: return
    data = {"token": PUSHPLUS_TOKEN, "title": f"基金日报 {datetime.date.today()}", 
            "content": content.replace("\n", "<br>"), "template": "html"}
    requests.post("http://www.pushplus.plus/send", json=data)

if __name__ == "__main__":
    now = datetime.datetime.now()
    
    # 1. 基础过滤：周日和周一零点不发 (逻辑同前)
    if now.weekday() == 0 or now.weekday() == 6:
        print("📅 昨夜市场休市，脚本休息。")
        exit()

    # 2. 核心补丁：检查今天是否为 A 股交易日
    try:
        # 获取最新的交易日历
        trade_conf = ak.tool_trade_date_hist_sinajs()
        trade_days = trade_conf['trade_date'].astype(str).tolist()
        
        # 判定“昨天”（即零点运行所对应的报告日）是否在交易日列表里
        # 注意：零点运行算的是前一天的数据，所以减去 1 天
        report_date = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        if report_date not in trade_days:
            print(f"🏮 判定 {report_date} 为法定节假日或休市日，春节快乐，不推送报告。")
            exit()
    except Exception as e:
        print(f"⚠️ 日历检查失败，尝试跳过: {e}")

    # ... 后续逻辑 (calculate_portfolio 等) ...

    recommends = get_fund_recommends()
    prompt = f"请分析持仓并对比市场表现：\n\n{report_text}\n\n{recommends}"
    ai_advice = ask_ai(prompt)

    push_to_wechat(f"{report_text}\n\n{recommends}\n\n### 🤖 AI 建议\n{ai_advice}")
    print("✅ 报告已发送！")
