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
    """核算持仓盈亏，返回报告文本和今日盈亏数值"""
    print("📊 正在同步持仓净值...")
    report = "❌ 持仓核算失败"
    daily_p = 0
    if not os.path.exists(PORTFOLIO_FILE):
        return "❌ 找不到持仓文件 portfolio.json", 0
    try:
        with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        total_val, total_daily_p, total_hold_p = 0, 0, 0
        lines = []
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
        report = f"💰 【资产汇总】\n总市值: {total_val:.2f} 元\n今日预估盈亏: {total_daily_p:+.2f} 元\n累计持有盈亏: {total_hold_p:+.2f} 元\n" + "--------------------------\n" + "\n".join(lines)
        return report, total_daily_p
    except Exception as e:
        return f"❌ 核算逻辑崩溃: {e}", 0

def get_market_data():
    """获取量化数据并计算综合得分"""
    print("🔎 正在执行全市场量化扫描...")
    try:
        df = ak.fund_open_fund_rank_em(symbol="全部")
        # 防御性检查：确保必要字段存在
        needed = ['基金类型', '夏普比率', '今年以来', '日增长率']
        if not all(col in df.columns for col in needed):
            return pd.DataFrame()
        # 筛选类型并清洗数据
        df = df[df['基金类型'].str.contains('股票型|混合型|指数型', na=False)].copy()
        for col in ['夏普比率', '今年以来', '日增长率']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        # 计算综合得分：夏普40% + 今年收益40% + 今日热度20%
        df['综合得分'] = (df['夏普比率'] * 0.4) + (df['今年以来'] * 0.4) + (df['日增长率'] * 0.2)
        return df.sort_values(by='综合得分', ascending=False)
    except:
        return pd.DataFrame()

def check_alternatives(market_df):
    """同类更优对比"""
    if market_df.empty or '综合得分' not in market_df.columns:
        return "⚠️ 暂无PK数据"
    try:
        with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        report = ["🔄 【同类更优监控】"]
        for item in data['holdings']:
            my_f = market_df[market_df['基金代码'] == item['code']]
            if my_f.empty: continue
            f_type = my_f.iloc[0]['基金类型']
            my_score = my_f.iloc[0]['综合得分']
            best = market_df[market_df['基金类型'] == f_type].head(5).iloc[0]
            if best['基金代码'] != item['code'] and best['综合得分'] > my_score * 1.2:
                report.append(f"● {item['name']}(分:{my_score:.1f}) -> 建议调仓至:{best['基金简称']}(分:{best['综合得分']:.1f})")
        return "\n".join(report) if len(report) > 1 else "✅ 当前持仓在同类中均处于领先水平。"
    except:
        return "⚠️ 择优逻辑暂时不可用。"

def ask_ai(prompt):
    """调用 AI 进行诊断"""
    if not DEEPSEEK_API_KEY: return "❌ AI 密钥未配置"
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": "你是一位专业的量化投资顾问。请分析持仓并指出更优调仓选择。"},
                      {"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ AI 诊断中断: {e}"

def push_to_wechat(content):
    """推送通知"""
    if not PUSHPLUS_TOKEN: return
    data = {"token": PUSHPLUS_TOKEN, "title": f"基金诊断报告 {datetime.date.today()}", 
            "content": content.replace("\n", "<br>"), "template": "html"}
    requests.post("http://www.pushplus.plus/send", json=data)

# ================= 主程序 =================
if __name__ == "__main__":
    now = datetime.datetime.now()
    # 1. 基础周六周日不运行
    if now.weekday() == 0 or now.weekday() == 6:
        print("📅 昨夜休市，脚本跳过执行。")
        exit()

    # 2. 初始化变量防止 NameError
    report_text, daily_p = calculate_portfolio()
    
    # 3. 节假日静默判定：如果今日盈亏波幅几乎为0，说明没更新
    if abs(daily_p) < 0.01:
        print("🏮 检测到盈亏无波动，判定为节假日（春节）或休市，不推送。")
        exit()

    # 4. 量化分析与PK
    market_df = get_market_data()
    alt_text = check_alternatives(market_df)
    
    # 获取排行 Top5
    top_5_text = "📈 【量化综合最优排行】\n"
    if not market_df.empty:
        for i, row in market_df.head(5).iterrows():
            top_5_text += f"- {row['基金简称']}({row['基金代码']}): 得分 {row['综合得分']:.1f}\n"
    else:
        top_5_text += "⚠️ 暂时无法获取量化数据。"

    # 5. AI 分析
    full_prompt = f"请基于数据给出诊断：\n\n【持仓报告】\n{report_text}\n\n【同类更优】\n{alt_text}\n\n【全市场参考】\n{top_5_text}\n\n给出毒舌精准的建议。"
    ai_advice = ask_ai(full_prompt)

    # 6. 推送
    push_to_wechat(f"{report_text}\n\n{alt_text}\n\n{top_5_text}\n\n### 🤖 AI 调仓诊断\n{ai_advice}")
    print("✅ 执行完成。")
