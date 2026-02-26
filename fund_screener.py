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
    if not os.path.exists(PORTFOLIO_FILE): return "❌ 找不到持仓文件", 0
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
            hold_p = (nav - item['cost_price']) * item['shares']
            hold_r = (nav - item['cost_price']) / item['cost_price'] * 100
            
            total_val += current_val
            total_daily_p += daily_profit
            total_hold_p += hold_p
            lines.append(f"- {item['name']}({item['code']}): 今日 {daily_profit:+.2f}元 | 持有 {hold_p:+.2f}元({hold_r:+.2f}%)")
        except: continue
            
    summary = f"💰 【资产汇总】\n总市值: {total_val:.2f} 元\n今日预估盈亏: {total_daily_p:+.2f} 元\n累计持有盈亏: {total_hold_p:+.2f} 元\n"
    return summary + "--------------------------\n" + "\n".join(lines), total_daily_p

def get_market_data():
    """获取全市场数据并计算综合得分"""
    print("🔎 正在执行全市场量化扫描...")
    try:
        df = ak.fund_open_fund_rank_em(symbol="全部")
        df = df[df['基金类型'].str.contains('股票型|混合型|指数型', na=False)]
        
        # 转换数值
        for col in ['夏普比率', '今年以来', '日增长率']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        df = df.dropna(subset=['夏普比率', '今年以来'])
        
        # 核心算法：综合得分 = (夏普率*0.5) + (今年收益*0.3) + (今日表现*0.2)
        df['综合得分'] = (df['夏普比率'] * 0.5) + (df['今年以来'] * 0.3) + (df['日增长率'] * 0.2)
        return df
    except:
        return pd.DataFrame()

def check_alternatives(portfolio_data, market_df):
    """同类更优监控"""
    if market_df.empty: return "⚠️ 暂时无法执行同类对比"
    
    report = ["🔄 【同类择优监控】"]
    for item in portfolio_data['holdings']:
        try:
            my_f = market_df[market_df['基金代码'] == item['code']]
            if my_f.empty: continue
            
            f_type = my_f.iloc[0]['基金类型']
            my_score = my_f.iloc[0]['综合得分']
            
            # 寻找同类型中得分最高的擂主
            best = market_df[market_df['基金类型'] == f_type].nlargest(1, '综合得分').iloc[0]
            
            if best['基金代码'] != item['code'] and best['综合得分'] > my_score * 1.1:
                report.append(f"● {item['name']} (得分 {my_score:.1f})\n  📍 建议关注更优同类: {best['基金简称']}({best['基金代码']}) 得分: {best['综合得分']:.1f}")
        except: continue
    
    return "\n".join(report) if len(report) > 1 else "✅ 您的持仓在同类中表现优秀，暂无需换仓建议。"

def ask_ai(prompt):
    if not DEEPSEEK_API_KEY: return "❌ AI 密钥未配置"
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": "你是一位专业的量化投资顾问。分析时要毒舌、精准，重点指出哪些该卖，哪些该换。"},
                      {"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ AI 分析失败: {e}"

def push_to_wechat(content):
    if not PUSHPLUS_TOKEN: return
    data = {"token": PUSHPLUS_TOKEN, "title": f"基金PK周报 {datetime.date.today()}", 
            "content": content.replace("\n", "<br>"), "template": "html"}
    requests.post("http://www.pushplus.plus/send", json=data)

if __name__ == "__main__":
    now = datetime.datetime.now()
    if now.weekday() == 0 or now.weekday() == 6:
        print("📅 昨夜休市，脚本跳过执行。")
        exit()

    # 1. 执行核算
    report_text, daily_p = calculate_portfolio()
    
    # 2. 节假日判定 (如果今日盈亏为 0)
    if abs(daily_p) < 0.01:
        print("🏮 净值未更新，判定为假期，取消推送。")
        exit()

    # 3. 深度量化对比
    market_df = get_market_data()
    with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
        p_data = json.load(f)
    alt_text = check_alternatives(p_data, market_df)
    
    # 4. 全市场 Top5 榜单
    top_5 = market_df.nlargest(5, '综合得分')
    top_text = "🏆 【全市场综合最优 Top5】\n" + "\n".join([f"- {r['基金简称']}({r['基金代码']}) 分数:{r['综合得分']:.1f}" for _,r in top_5.iterrows()])

    # 5. AI 分析
    prompt = f"我的持仓分析：\n{report_text}\n\n{alt_text}\n\n{top_text}\n\n请告诉我：哪些基金表现拉胯？针对同类最优建议，我该现在换仓吗？"
    ai_advice = ask_ai(prompt)

    # 6. 推送
    push_to_wechat(f"{report_text}\n\n{alt_text}\n\n{top_text}\n\n### 🤖 AI 调仓毒舌点评\n{ai_advice}")
    print("✅ 任务完成！")
