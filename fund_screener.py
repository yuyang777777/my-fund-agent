import os
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
    """核算持仓收益，确保报告文本始终生成"""
    print("📊 正在同步持仓数据...")
    report = "⚠️ 持仓核算未完成"
    daily_p = 0
    if not os.path.exists(PORTFOLIO_FILE):
        return "❌ 找不到 portfolio.json 文件", 0
    
    try:
        with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        total_val, total_daily_p, total_hold_p = 0, 0, 0
        lines = []
        for item in data['holdings']:
            try:
                # 获取实时净值数据
                df = ak.fund_open_fund_info_em(symbol=item['code'], indicator="单位净值走势")
                nav = float(df.iloc[-1]['单位净值'])
                prev_nav = float(df.iloc[-2]['单位净值'])
                
                # 计算盈亏
                c_val = nav * item['shares']
                d_profit = c_val * ((nav - prev_nav) / prev_nav)
                h_profit = (nav - item['cost_price']) * item['shares']
                h_rate = (nav - item['cost_price']) / item['cost_price'] * 100
                
                total_val += c_val
                total_daily_p += d_profit
                total_hold_p += h_profit
                lines.append(f"- {item['name']}({item['code']}): 今日 {d_profit:+.2f}元 | 持有 {h_profit:+.2f}元({h_rate:+.2f}%)")
            except: continue
            
        report = (f"💰 【资产汇总】\n总市值: {total_val:.2f} 元\n"
                  f"今日预估盈亏: {total_daily_p:+.2f} 元\n"
                  f"累计持有盈亏: {total_hold_p:+.2f} 元\n"
                  "--------------------------\n" + "\n".join(lines))
        return report, total_daily_p
    except Exception as e:
        return f"❌ 核算逻辑崩溃: {e}", 0

def get_market_data():
    """获取量化优选数据，支持 ETF 联接基金 PK"""
    print("🔎 正在执行全量化扫描...")
    try:
        # 获取全量排行
        df = ak.fund_open_fund_rank_em(symbol="全部")
        
        # 1. 字段重命名，防止 KeyError
        df.rename(columns={'今年来': '今年以来', '日增长率': '今日涨幅'}, inplace=True)
        
        # 2. 扩大筛选范围，包含指数/联接基金
        df = df[df['基金类型'].str.contains('股票型|混合型|指数型|ETF联接|QDII', na=False)].copy()
        
        # 3. 数据清洗与格式化
        for col in ['今年以来', '今日涨幅', '近1年', '近3年']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        # 4. 综合评分算法 (今年40% + 近1年30% + 近3年30%)
        df['综合得分'] = (df['今年以来'] * 0.4) + (df.get('近1年', 0) * 0.3) + (df.get('近3年', 0) * 0.3)
        
        # 5. 代码补全：统一转为 6 位字符串
        df['基金代码'] = df['基金代码'].astype(str).str.zfill(6)
        
        return df.sort_values(by='综合得分', ascending=False)
    except Exception as e:
        print(f"❌ 量化数据抓取失败: {e}")
        return pd.DataFrame()

def check_alternatives(market_df):
    """强制执行 1对1 同类择优 PK"""
    print("🔄 正在执行同类择优监控...")
    if market_df.empty: return "⚠️ 暂时无法获取PK对比数据"
    
    try:
        with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        pk_lines = ["🔄 【同类择优监控】"]
        for item in data['holdings']:
            code = str(item['code']).zfill(6)
            my_fund = market_df[market_df['基金代码'] == code]
            
            if not my_fund.empty:
                my_row = my_fund.iloc[0]
                f_type = my_row['基金类型']
                my_score = my_row['综合得分']
                
                # 寻找同类型中排名第 1 的基金
                best_in_class = market_df[market_df['基金类型'] == f_type].head(1).iloc[0]
                
                if best_in_class['基金代码'] != code:
                    gap = best_in_class['综合得分'] - my_score
                    pk_lines.append(f"● {item['name']}(分:{my_score:.1f}) -> 同类最强:{best_in_class['基金简称']}(分:{best_in_class['综合得分']:.1f})")
            else:
                pk_lines.append(f"● {item['name']}: ⚠️ 表现极差或数据未更新，未入榜")
        
        return "\n".join(pk_lines) if len(pk_lines) > 1 else "✅ 当前持仓均为同类佼佼者。"
    except:
        return "⚠️ PK逻辑执行受限"

def ask_ai(prompt):
    """请求 AI 诊断"""
    if not DEEPSEEK_API_KEY: return "❌ AI 密钥未配置"
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": "你是一个专业的量化基金分析师。请针对用户的持仓与市场最强基金的差距，给出犀利的调仓建议。"},
                      {"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except: return "🤖 AI 暂时无法连接"

def push_to_wechat(content):
    """推送至微信"""
    if not PUSHPLUS_TOKEN: return
    data = {"token": PUSHPLUS_TOKEN, "title": f"基金诊断报告 {datetime.date.today()}", 
            "content": content.replace("\n", "<br>"), "template": "html"}
    requests.post("http://www.pushplus.plus/send", json=data)

# ================= 主程序 =================
if __name__ == "__main__":
    now = datetime.datetime.now()
    if now.weekday() >= 5: # 周末休市不运行
        print("📅 周末休市中。")
        exit()

    # 1. 核算持仓
    report_text, daily_p = calculate_portfolio()
    
    # 2. 假期静默判定
    if abs(daily_p) < 0.01:
        print("🏮 检测到无盈亏波动，判定为休市。")
        exit()

    # 3. 执行分析
    market_df = get_market_data()
    pk_text = check_alternatives(market_df)
    
    # 获取前 5 名
    top_5_text = "🏆 【全市场量化综合最优排行】\n"
    if not market_df.empty:
        for i, r in market_df.head(5).iterrows():
            top_5_text += f"- {r['基金简称']}({r['基金代码']}): 得分 {r['综合得分']:.1f}\n"

    # 4. AI 诊断并推送
    full_prompt = f"持仓数据：\n{report_text}\n\nPK对比结果：\n{pk_text}\n\n量化排行：\n{top_5_text}\n\n给出操作建议。"
    ai_advice = ask_ai(full_prompt)
    
    push_to_wechat(f"{report_text}\n\n{pk_text}\n\n{top_5_text}\n\n### 🤖 AI 调仓建议\n{ai_advice}")
    print("✅ 报告已发送！")
