import akshare as ak
import pandas as pd
import numpy as np
import json
import datetime
import os
import time
import socket
import requests # 新增：用于微信推送
from openai import OpenAI

# ================= 配置区域 =================
# 从环境变量读取 Key，保护隐私安全
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

PORTFOLIO_FILE = 'portfolio.json'
TARGET_COUNT = 5          
START_1Y_RETURN = 8.0     
START_MAX_DD = 20.0       

# ============================================

# 初始化 Gemini 客户端

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
def ask_ai(prompt):
    print("\n🤖 正在请求 DeepSeek-V3 进行深度分析...")
    if not DEEPSEEK_API_KEY:
        return "❌ 错误：未配置 DEEPSEEK_API_KEY"

    # DeepSeek 兼容 OpenAI SDK 格式
    client_ds = OpenAI(
        api_key=DEEPSEEK_API_KEY, 
        base_url="https://api.deepseek.com"
    )

    for i in range(3): # 简单重试 3 次
        try:
            response = client_ds.chat.completions.create(
                model="deepseek-chat", # 对应最新的 DeepSeek-V3
                messages=[
                    {"role": "system", "content": "你是一位专业的量化投资专家，请基于数据给出冷静、客观的诊断。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1024
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"⚠️ 第 {i+1} 次尝试失败: {e}")
            time.sleep(5)
    return "❌ DeepSeek 调用失败，请检查 API 余额或网络。"

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

# ... 前面的函数定义保持不变 ...

if __name__ == "__main__":
    # 1. 自动判定休市：周六和周日不工作
    # weekday() 返回 0-6，5 是周六，6 是周日
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        print(f"📅 今天是星期{now.weekday()+1}（周末），市场不开启，助手补觉中... 💤")
        exit()

    print("🚀 交易日到达，开始量化诊断...")

    # 2. 执行持仓核算
    portfolio_report = calculate_portfolio()
    
    # 3. 再次检查数据有效性
    # 如果总资产为 0 或者今日盈亏完全没变动，可能是节假日或数据源未更新
    if "今日预估盈亏: +0.00" in portfolio_report:
        print("⏸️ 检测到今日盈亏无波动，可能是法定节假日，跳过今日推送。")
        exit()

    # 4. 获取量化优选建议
    recommends_str = get_fund_recommends()

    # 5. 组合 Prompt 并调用 AI (此处确保你已将函数名改为 ask_ai)
    prompt = f"""
    你是资深基金经理，请基于以下数据给出简明扼要的分析（300字以内）：
    
    【我的持仓】
    {portfolio_report}
    
    【市场优选参考】
    {recommends_str}
    
    请重点告知：哪些持仓需要止盈/止损？目前行情适合加仓哪类板块？
    """
    
    ai_advice = ask_ai(prompt) # 确保你已经把之前的 ask_gemini 改成了支持 DeepSeek/Grok 的 ask_ai

    # 6. 最终汇总并推送到微信
    final_report = f"📅 基金日报 {now.strftime('%Y-%m-%d')}\n\n{portfolio_report}\n\n### 💡 AI 投资建议\n{ai_advice}"
    push_to_wechat(final_report)
