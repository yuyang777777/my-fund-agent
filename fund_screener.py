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
    print("🔎 正在筛选全市场涨幅排行...")
    try:
        # 使用排行插件，在零点运行更稳定
        df = ak.fund_open_fund_rank_em(symbol="全部")
        df = df[df['基金类型'].str.contains('股票型|混合型|指数型', na=False)]
        df['日增长率'] = pd.to_numeric(df['日增长率'], errors='coerce')
        top_funds = df.nlargest(5, '日增长率')
        
        res = ["📈 【量化优选参考】"]
        for _, row in top_funds.iterrows():
            res.append(f"- {row['基金简称']}({row['基金代码']}): 今日涨幅 {row['日增长率']}%")
        return "\n".join(res)
    except:
        return "⚠️ 暂未获取到量化数据"

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
    weekday = now.weekday() 
    
    # 核心逻辑：周日(6)和周一(0)的零点不推送（因为对应周六日的休市数据）
    if weekday == 0 or weekday == 6:
        print("📅 昨夜市场休市，脚本跳过执行。")
        exit()

    report_text, daily_p = calculate_portfolio()
    
    # 如果今日盈亏为0且不是因为你没买，那说明是节假日没更新
    if abs(daily_p) < 0.01:
        print("⏸️ 净值未更新（可能是法定节假日），跳过推送。")
        exit()

    recommends = get_fund_recommends()
    prompt = f"请分析持仓并对比市场表现：\n\n{report_text}\n\n{recommends}"
    ai_advice = ask_ai(prompt)

    push_to_wechat(f"{report_text}\n\n{recommends}\n\n### 🤖 AI 建议\n{ai_advice}")
    print("✅ 报告已发送！")
