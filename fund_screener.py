import os
import time
import datetime
import json
import pandas as pd
import akshare as ak
import requests
from openai import OpenAI

# ================= 配置区域 =================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")
PORTFOLIO_FILE = "portfolio.json"

def calculate_portfolio():
    if not os.path.exists(PORTFOLIO_FILE): return "影子账户数据文件不存在", 0
    with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    total_val = 0
    total_daily_p = 0
    total_hold_p = 0
    lines = []
    
    for item in data['holdings']:
        try:
            # 抓取基金净值
            df = ak.fund_open_fund_info_em(symbol=item['code'], indicator="单位净值走势")
            nav = float(df.iloc[-1]['单位净值'])
            prev_nav = float(df.iloc[-2]['单位净值'])
            
            # 核算收益
            daily_rate = (nav - prev_nav) / prev_nav
            current_val = nav * item['shares']
            daily_profit = current_val * (daily_rate / (1 + daily_rate))
            
            hold_profit = (nav - item['cost_price']) * item['shares']
            hold_rate = (nav - item['cost_price']) / item['cost_price'] * 100
            
            total_val += current_val
            total_daily_p += daily_profit
            total_hold_p += hold_profit
            
            lines.append(f"- {item['name']}({item['code']}): 今日 {daily_profit:+.2f}元 | 持有 {hold_profit:+.2f}元({hold_rate:+.2f}%)")
        except:
            continue
            
    summary = (
        f"💰 【资产汇总】\n"
        f"总市值: {total_val:.2f} 元\n"
        f"今日预估盈亏: {total_daily_p:+.2f} 元\n"
        f"累计持有盈亏: {total_hold_p:+.2f} 元\n"
        f"--------------------------\n"
    )
    return summary + "\n".join(lines), total_daily_p

def get_fund_recommends():
    try:
        df = ak.fund_value_estimation_em()
        df = df[df['基金类型'].str.contains('混合|指数', na=False)]
        df['日增长率'] = pd.to_numeric(df['日增长率'], errors='coerce')
        top_funds = df.nlargest(5, '日增长率')
        res = ["📈 【量化参考】"]
        for _, row in top_funds.iterrows():
            res.append(f"- {row['基金名称']}({row['基金代码']}): 估算涨幅 {row['日增长率']}%")
        return "\n".join(res)
    except:
        return "⚠️ 暂未获取到量化优选数据"

def ask_ai(prompt):
    if not DEEPSEEK_API_KEY: return "❌ 未配置 DEEPSEEK_API_KEY"
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ AI 分析失败: {e}"

def push_to_wechat(content):
    if not PUSHPLUS_TOKEN: return
    url = "http://www.pushplus.plus/send"
    # 微信端换行需要 <br>
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": f"基金日报 {datetime.date.today()}",
        "content": content.replace("\n", "<br>"),
        "template": "html"
    }
    requests.post(url, json=data)

if __name__ == "__main__":
    now = datetime.datetime.now()
    # 1. 周末不运行 (5=周六, 6=周日)
    if now.weekday() >= 5:
        print("📅 周末休市，脚本自动休眠。")
        exit()

    # 2. 执行核算
    report_text, daily_p = calculate_portfolio()
    
    # 3. 节假日/未更新判定 (今日盈亏为0则不推送)
    if abs(daily_p) < 0.01:
        print("⏸️ 净值数据未更新（可能是节假日），取消推送。")
        exit()

    # 4. 获取推荐并调用 AI
    recommends = get_fund_recommends()
    prompt = f"请作为量化专家分析以下持仓数据，给出操作建议：\n{report_text}\n\n参考量化数据：\n{recommends}"
    ai_advice = ask_ai(prompt)

    # 5. 推送
    final_content = f"{report_text}\n\n{recommends}\n\n### 🤖 AI 投资建议\n{ai_advice}"
    push_to_wechat(final_content)
    print("✅ 任务完成，报告已送达微信。")
