import os
import re
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
    """核算持仓收益，返回报告文本和今日盈亏数值"""
    print("📊 正在同步持仓数据...")
    if not os.path.exists(PORTFOLIO_FILE):
        return "❌ 找不到 portfolio.json 文件", 0

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

                c_val = nav * item['shares']
                d_profit = c_val * ((nav - prev_nav) / prev_nav)
                h_profit = (nav - item['cost_price']) * item['shares']
                h_rate = (nav - item['cost_price']) / item['cost_price'] * 100

                total_val += c_val
                total_daily_p += d_profit
                total_hold_p += h_profit
                lines.append(
                    f"- {item['name']}({item['code']}): "
                    f"今日 {d_profit:+.2f}元 | 持有 {h_profit:+.2f}元({h_rate:+.2f}%)"
                )
            except:
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
    """获取全市场量化数据，自动适配 akshare 实际列名"""
    print("🔎 正在执行全量化扫描...")
    try:
        df = ak.fund_open_fund_rank_em(symbol="全部")

        # 打印真实列名，方便 Actions 日志排查
        print(f"📋 akshare 实际返回列名：{df.columns.tolist()}")

        # 筛选权益类基金
        if '基金类型' not in df.columns:
            print("❌ 找不到'基金类型'列，请查看上方日志确认真实列名")
            return pd.DataFrame()

        df = df[df['基金类型'].str.contains('股票型|混合型|指数型', na=False)].copy()

        # 将非标识列统一转为数值
        id_cols = {'基金代码', '基金简称', '基金类型'}
        for col in df.columns:
            if col not in id_cols:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # 动态定位"今年以来"列
        score_col = next(
            (c for c in ['今年以来', '今年来', '年初至今'] if c in df.columns), None
        )
        if score_col is None:
            print(f"❌ 找不到收益列，当前全部列名：{df.columns.tolist()}")
            return pd.DataFrame()

        # 动态定位近1年、近3年列（可选，缺失时忽略）
        year1_col = next((c for c in ['近1年', '近一年', '1年'] if c in df.columns), None)
        year3_col = next((c for c in ['近3年', '近三年', '3年'] if c in df.columns), None)

        df['综合得分'] = df[score_col] * 0.5
        if year1_col:
            df['综合得分'] += df[year1_col] * 0.3
        if year3_col:
            df['综合得分'] += df[year3_col] * 0.2

        df['基金代码'] = df['基金代码'].astype(str).str.zfill(6)

        result = df.sort_values(by='综合得分', ascending=False)
        print(f"✅ 量化扫描完成，共 {len(result)} 支基金入榜")
        return result

    except Exception as e:
        print(f"❌ 量化数据抓取失败：{e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()


def check_alternatives(market_df):
    """逐只持仓与同类最强基金进行 1对1 PK"""
    print("🔄 正在执行同类择优监控...")
    if market_df.empty:
        return "⚠️ 暂时无法获取PK对比数据"

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
                same_type = market_df[market_df['基金类型'] == f_type]
                if same_type.empty:
                    continue
                best = same_type.iloc[0]
                if best['基金代码'] != code:
                    pk_lines.append(
                        f"● {item['name']}(得分:{my_score:.1f}) "
                        f"→ 同类最强:{best['基金简称']}(得分:{best['综合得分']:.1f})"
                    )
                else:
                    pk_lines.append(f"● {item['name']}: ✅ 同类排名第一")
            else:
                pk_lines.append(f"● {item['name']}({code}): ⚠️ 未在排行榜中找到")
                print(f"  未匹配代码：{code}，榜单样例：{market_df['基金代码'].head(5).tolist()}")

        return "\n".join(pk_lines)

    except Exception as e:
        print(f"❌ PK逻辑报错：{e}")
        import traceback
        traceback.print_exc()
        return "⚠️ PK逻辑执行受限"


def ask_ai(prompt):
    """调用 DeepSeek 给出专业配置建议"""
    if not DEEPSEEK_API_KEY:
        return "❌ AI 密钥未配置"
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一位专业的基金投资顾问。请基于数据客观分析持仓结构，"
                        "从风险敞口、行业集中度、仓位配置等维度给出清晰、理性、"
                        "有建设性的优化建议，语气保持专业克制。"
                    )
                },
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ AI 诊断中断: {e}"


def markdown_to_html(text):
    """将 Markdown 语法转换为 HTML，避免 ### 和 ** 原样显示"""
    # 标题：### → <h3>
    text = re.sub(r'####\s(.+)', r'<h4>\1</h4>', text)
    text = re.sub(r'###\s(.+)', r'<h3>\1</h3>', text)
    text = re.sub(r'##\s(.+)', r'<h2>\1</h2>', text)
    # 粗体：**文字** → <strong>文字</strong>
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # 换行
    text = text.replace("\n", "<br>")
    return text


def push_to_wechat(content):
    """推送至微信（PushPlus）"""
    if not PUSHPLUS_TOKEN:
        return
    html_content = markdown_to_html(content)
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": f"基金诊断报告 {datetime.date.today()}",
        "content": html_content,
        "template": "html"
    }
    requests.post("http://www.pushplus.plus/send", json=data)


# ================= 主程序 =================
if __name__ == "__main__":
    now = datetime.datetime.now()

    # 周末不运行
    if now.weekday() >= 5:
        print("📅 周末休市中，脚本跳过执行。")
        exit()

    # 核算持仓
    report_text, daily_p = calculate_portfolio()

    # 节假日静默判定
    if abs(daily_p) < 0.01:
        print("🏮 检测到盈亏无波动，判定为节假日或休市，不推送。")
        exit()

    # 量化分析
    market_df = get_market_data()
    pk_text = check_alternatives(market_df)

    # 全市场 Top5
    top_5_text = "🏆 【全市场量化综合最优排行】\n"
    if not market_df.empty:
        for _, r in market_df.head(5).iterrows():
            top_5_text += f"- {r['基金简称']}({r['基金代码']}): 得分 {r['综合得分']:.1f}\n"
    else:
        top_5_text += "⚠️ 暂时无法获取量化数据。"

    # AI 诊断
    full_prompt = (
        f"持仓数据：\n{report_text}\n\n"
        f"同类PK结果：\n{pk_text}\n\n"
        f"全市场量化排行：\n{top_5_text}\n\n"
        f"请从风险敞口、行业集中度和仓位配置角度，给出专业、客观的优化建议。"
    )
    ai_advice = ask_ai(full_prompt)

    # 推送
    push_to_wechat(
        f"{report_text}\n\n{pk_text}\n\n{top_5_text}\n\n"
        f"### 🤖 AI 调仓建议\n{ai_advice}"
    )
    print("✅ 报告已发送！")
