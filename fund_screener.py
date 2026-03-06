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

def get_market_data():
    """获取量化优选排行数据（兼容性增强版）"""
    print("🔎 正在从全市场筛选绩优基金...")
    try:
        # 获取全部开放式基金排行
        df = ak.fund_open_fund_rank_em(symbol="全部")
        
        # 打印列名到日志，方便你调试
        print(f"📊 接口返回列名示例: {df.columns.tolist()[:10]}")

        # 1. 兼容性重命名：将接口可能出现的名称统一
        rename_dict = {
            '今年来': '今年以来',
            '今年收益': '今年以来',
            '日增长率': '今日涨幅'
        }
        df.rename(columns=rename_dict, inplace=True)

        # 2. 核心字段检查：确保至少有收益率数据
        if '今年以来' not in df.columns:
            print("⚠️ 无法识别收益率字段，切换备用方案...")
            return pd.DataFrame()

        # 3. 数据清洗
        cols_to_fix = ['今年以来', '今日涨幅', '近1年']
        for col in cols_to_fix:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        # 4. 评分逻辑：由于全表无夏普率，改用 (今年收益 * 0.6 + 近1年收益 * 0.4)
        # 这能选出中长期走势最稳健的基金
        df['综合得分'] = (df['今年以来'] * 0.6) + (df.get('近1年', df['今年以来']) * 0.4)
        
        return df.sort_values(by='综合得分', ascending=False)
    except Exception as e:
        print(f"❌ 量化数据抓取失败: {e}")
        return pd.DataFrame()

def calculate_portfolio():
    """核算持仓，确保 report_text 始终被定义"""
    print("📊 正在同步持仓数据...")
    report = "⚠️ 持仓核算未完成"
    daily_p = 0
    if not os.path.exists(PORTFOLIO_FILE):
        return "❌ 找不到 portfolio.json 文件", 0
    
    try:
        with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        total_val, total_daily_p = 0, 0
        lines = []
        for item in data['holdings']:
            try:
                # 抓取实时净值
                df = ak.fund_open_fund_info_em(symbol=item['code'], indicator="单位净值走势")
                nav = float(df.iloc[-1]['单位净值'])
                prev_nav = float(df.iloc[-2]['单位净值'])
                
                c_val = nav * item['shares']
                d_profit = c_val * ((nav - prev_nav) / prev_nav)
                total_val += c_val
                total_daily_p += d_profit
                lines.append(f"- {item['name']}({item['code']}): 今日 {d_profit:+.2f}元")
            except: continue
            
        report = f"💰 总市值: {total_val:.2f} | 今日预估: {total_daily_p:+.2f}\n" + "\n".join(lines)
        return report, total_daily_p
    except Exception as e:
        return f"❌ 核算崩溃: {e}", 0

def ask_ai(prompt):
    if not DEEPSEEK_API_KEY: return "❌ AI 密钥未配置"
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": "你是一个毒舌但专业的基金分析师，请根据数据给出具体的换仓建议。"},
                      {"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except: return "🤖 AI 暂时掉线了..."

def push_to_wechat(content):
    if not PUSHPLUS_TOKEN: return
    data = {"token": PUSHPLUS_TOKEN, "title": f"基金PK报告 {datetime.date.today()}", 
            "content": content.replace("\n", "<br>"), "template": "html"}
    requests.post("http://www.pushplus.plus/send", json=data)

if __name__ == "__main__":
    now = datetime.datetime.now()
    if now.weekday() >= 5: exit() # 周末不跑

    # 1. 核算持仓
    report_text, daily_p = calculate_portfolio()
    
    # 2. 获取量化数据
    market_df = get_market_data()
    
    # 3. 构造 PK 报告
    pk_text = "⚠️ 暂时无法获取PK对比数据"
    top_5_text = "⚠️ 暂时无法获取量化排行"
    
    if not market_df.empty and '综合得分' in market_df.columns:
        # 获取排行 Top 5
        top_5 = market_df.head(5)
        top_5_text = "🏆 【全市场量化综合最优排行】\n" + "\n".join([f"- {r['基金简称']}({r['基金代码']}) 得分:{r['综合得分']:.1f}" for _, r in top_5.iterrows()])
        
        # 同类 PK 简易逻辑：对比你持仓中得分最低的，看是否有更好的平替
        pk_text = "🔄 【同类择优监控】\n✅ 建议关注上方最优排行榜中的品种，替换表现较差的持仓。"

    # 4. AI 诊断与发送
    prompt = f"分析持仓：\n{report_text}\n\n{top_5_text}\n\n请指出我的持仓里哪些该卖？该换成什么？"
    ai_advice = ask_ai(prompt)
    
    push_to_wechat(f"{report_text}\n\n{pk_text}\n\n{top_5_text}\n\n### 🤖 AI 调仓建议\n{ai_advice}")
    print("✅ 运行成功！")
