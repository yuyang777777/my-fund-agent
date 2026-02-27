import os
import time
import datetime
import json
import pandas as pd
import akshare as ak
import requests
from openai import OpenAI

# ================= 配置区域 =================
# 请确保在 GitHub Secrets 中配置了以下变量
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")
PORTFOLIO_FILE = "portfolio.json"

def calculate_portfolio():
    """核算持仓盈亏"""
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
                # 获取净值历史
                df = ak.fund_open_fund_info_em(symbol=item['code'], indicator="单位净值走势")
                nav = float(df.iloc[-1]['单位净值'])
                prev_nav = float(df.iloc[-2]['单位净值'])
                
                # 计算数据
                daily_rate = (nav - prev_nav) / prev_nav
                current_val = nav * item['shares']
                daily_profit = current_val * (daily_rate / (1 + daily_rate))
                hold_profit = (nav - item['cost_price']) * item['shares']
                hold_rate = (nav - item['cost_price']) / item['cost_price'] * 100
                
                total_val += current_val
                total_daily_p += daily_profit
                total_hold_p += hold_profit
                lines.append(f"- {item['name']}({item['code']}): 今日 {daily_profit:+.2f}元 | 持有 {hold_profit:+.2f}元({hold_rate:+.2f}%)")
            except Exception as e:
                print(f"⚠️ 基金 {item['code']} 同步失败: {e}")
                continue
                
        report = (
            f"💰 【资产汇总】\n"
            f"总市值: {total_val:.2f} 元\n"
            f"今日预估盈亏: {total_daily_p:+.2f} 元\n"
            f"累计持有盈亏: {total_hold_p:+.2f} 元\n"
            f"--------------------------\n"
            + "\n".join(lines)
        )
        return report, total_daily_p
    except Exception as e:
        return f"❌ 核算逻辑崩溃: {e}", 0

def get_market_data():
    """获取量化优选排行数据（含防御性逻辑）"""
    print("🔎 正在执行全市场量化扫描...")
    try:
        # 获取排行接口
        df = ak.fund_open_fund_rank_em(symbol="全部")
        
        # 1. 检查必要列，防止 KeyError
        needed = ['基金类型', '夏普比率', '今年以来', '日增长率']
        if not all(col in df.columns for col in needed):
            print(f"⚠️ 接口数据不全，当前列名: {df.columns.tolist()[:10]}")
            return pd.DataFrame()

        # 2. 筛选主要类型
        df = df[df['基金类型'].str.contains('股票型|混合型|指数型', na=False)].copy()
        
        # 3. 数据转换与评分
        for col in ['夏普比率', '今年以来', '日增长率']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        # 核心算法：得分 = 夏普40% + 今年收益40% + 今日表现20%
        df['综合得分'] = (df['夏普比率'] * 0.4) + (df['今年以来'] * 0.4) + (df['日增长率'] * 0.2)
        
        return df.sort_values(by='综合得分', ascending=False)
    except Exception as e:
        print(f"❌ 量化扫描失败: {e}")
        return pd.DataFrame()

def check_alternatives(market_df):
    """监测持仓是否有同类更优"""
    print("🔄 正在执行同类择优监控...")
    if market_df.empty or '综合得分' not in market_df.columns:
        return "⚠️ 暂时无法获取PK数据"
        
    try:
        with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        report = ["🔄 【同类更优监控】"]
        for item in data['holdings']:
            # 找到持仓基金在市场里的表现
            my_f = market_df[market_df['基金代码'] == item['code']]
            if my_f.empty: continue
            
            f_type = my_f.iloc[0]['基金类型']
            my_score = my_f.iloc[0]['综合得分']
            
            # 找到同类得分最高的“擂主”
            best = market_df[market_df['基金类型'] == f_type].head(10).iloc[0]
            
            # 如果擂主不是自己，且得分高出 20%，则预警
            if best['基金代码'] != item['code'] and best['综合得分'] > my_score * 1.2:
                report.append(f"● {item['name']}(分:{my_score:.1f}) -> 建议调仓至:{best['基金简称']}(分:{best['综合得分']:.1f})")
                
        return "\n".join(report) if len(report) > 1 else "✅ 当前持仓在同类中均处于领先水平。"
    except Exception as e:
        return f"⚠️ 择优逻辑出错: {e}"

def ask_ai(prompt):
    """调用 DeepSeek 进行智能分析"""
    if not DEEPSEEK_API_KEY: return "❌ AI 密钥未配置"
    print("🤖 正在请求 AI 投资建议...")
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一位专业的量化投资顾问。请分析持仓风险，对比市场优选，给出具体的换仓策略。"},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ AI 诊断中断: {e}"

def push_to_wechat(content):
    """发送微信通知"""
    if not PUSHPLUS_TOKEN: return
    print("📲 正在发送微信报告...")
    data = {
        "token": PUSHPLUS_TOKEN, 
        "title": f"基金诊断报告 {datetime.date.today()}", 
        "content": content.replace("\n", "<br>"), 
        "template": "html"
    }
    requests.post("http://www.pushplus.plus/send", json=data)

# ================= 主程序 =================
if __name__ == "__main__":
    now = datetime.datetime.now()
    
    # 1. 基础过滤：周六、周日休市
      if now.weekday() == 0 or now.weekday() == 6:
        print("📅 昨夜休市，脚本跳过执行。")
        exit()

    # 2. 核算持仓 (初始化变量，防止后续 NameError)
    report_text, daily_p = calculate_portfolio()
    
    # 3. 核心判定：春节/法定节假日判定
    # 如果总盈亏波动的绝对值小于 0.01，判定为无数据更新，直接退出
    if abs(daily_p) < 0.01:
        print("🏮 检测到净值盈亏为 0，判定为节假日（春节）或数据未更新，停止推送。")
        exit()

    # 4. 执行深度量化分析
    market_df = get_market_data()
    alt_text = check_alternatives(market_df)
    
    # 获取量化优选 Top 5
    top_5_text = "📈 【全市场量化最优排行】\n"
    if not market_df.empty:
        for i, row in market_df.head(5).iterrows():
            top_5_text += f"- {row['基金简称']}({row['基金代码']}): 综合得分 {row['综合得分']:.1f}\n"
    else:
        top_5_text += "⚠️ 暂时无法获取今日量化优选数据。"

    # 5. 组合 Prompt 并询问 AI
    full_prompt = (
        f"请基于以下数据给出投资诊断：\n\n"
        f"【持仓盈亏报告】\n{report_text}\n\n"
        f"【同类更优对比】\n{alt_text}\n\n"
        f"【市场量化参考】\n{top_5_text}\n\n"
        f"重点回答：是否有基金需要立即卖出？推荐的更优基金是否值得调仓？"
    )
    ai_advice = ask_ai(full_prompt)

    # 6. 推送
    push_to_wechat(f"{report_text}\n\n{alt_text}\n\n{top_5_text}\n\n### 🤖 AI 调仓诊断\n{ai_advice}")
    print("✅ 诊断完成，报告已成功发送！")
