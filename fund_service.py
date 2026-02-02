"""
基金实时估值服务 - 后端API
使用 Flask + AKShare 实现基金数据获取和管理
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import akshare as ak
import pandas as pd
import json
import os
from datetime import datetime
import threading
import time

app = Flask(__name__)
CORS(app)  # 允许跨域请求

# 数据文件路径
DATA_FILE = 'fund_data.json'

# 全局数据存储
fund_holdings = []  # 持仓数据
cache_data = {}  # 缓存估值数据
cache_timestamp = None


def load_holdings():
    """加载历史持仓数据"""
    global fund_holdings
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                fund_holdings = json.load(f)
            print(f"✅ 加载了 {len(fund_holdings)} 个基金持仓")
        except Exception as e:
            print(f"❌ 加载数据失败: {e}")
            fund_holdings = []
    else:
        print("📝 数据文件不存在，使用空列表")
        fund_holdings = []


def save_holdings():
    """保存持仓数据到文件"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(fund_holdings, f, ensure_ascii=False, indent=2)
        print(f"💾 已保存 {len(fund_holdings)} 个基金持仓")
        return True
    except Exception as e:
        print(f"❌ 保存数据失败: {e}")
        return False


def fetch_fund_estimation(fund_codes):
    """
    获取基金实时估值数据
    
    Args:
        fund_codes: 基金代码列表
        
    Returns:
        dict: 基金代码 -> 估值数据的映射
    """
    if not fund_codes:
        return {}
    
    try:
        # 获取混合型基金估值数据
        print(f"🔄 正在获取基金估值数据...")
        fund_value_estimation_em_df = ak.fund_value_estimation_em(symbol="混合型")
        
        # 筛选用户关注的基金
        res_df = fund_value_estimation_em_df[
            fund_value_estimation_em_df['基金代码'].isin(fund_codes)
        ]
        
        result = {}
        for _, row in res_df.iterrows():
            code = row['基金代码']
            result[code] = {
                'code': code,
                'name': row['基金名称'],
                'estimated_value': float(row['2026-02-02-估算数据-估算值']) if row['2026-02-02-估算数据-估算值'] != '---' else None,
                'estimated_change_rate': str(row['2026-02-02-估算数据-估算增长率']),
                'unit_net_value': float(row['2026-01-30-单位净值']) if row['2026-01-30-单位净值'] != '---' else None,
                'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        
        print(f"✅ 成功获取 {len(result)} 个基金数据")
        return result
        
    except Exception as e:
        print(f"❌ 获取基金数据失败: {e}")
        return {}


def calculate_profit(holding):
    """计算持仓收益"""
    if holding['estimated_value'] is None:
        return {
            'total_cost': holding['cost_price'] * holding['shares'],
            'current_value': None,
            'profit': None,
            'profit_rate': None
        }
    
    total_cost = holding['cost_price'] * holding['shares']
    current_value = holding['estimated_value'] * holding['shares']
    profit = current_value - total_cost
    profit_rate = (profit / total_cost * 100) if total_cost > 0 else 0
    
    return {
        'total_cost': round(total_cost, 2),
        'current_value': round(current_value, 2),
        'profit': round(profit, 2),
        'profit_rate': round(profit_rate, 2)
    }


def update_cache():
    """更新缓存数据"""
    global cache_data, cache_timestamp
    
    fund_codes = [h['code'] for h in fund_holdings]
    if not fund_codes:
        cache_data = {}
        cache_timestamp = datetime.now()
        return
    
    # 获取估值数据
    estimation_data = fetch_fund_estimation(fund_codes)
    
    # 合并持仓数据和估值数据
    result = []
    for holding in fund_holdings:
        code = holding['code']
        item = {
            'id': holding['id'],
            'code': code,
            'name': holding.get('name', ''),
            'cost_price': holding['cost_price'],
            'shares': holding['shares'],
            'estimated_value': None,
            'estimated_change_rate': '---',
            'unit_net_value': None,
        }
        
        # 如果有估值数据，更新
        if code in estimation_data:
            est = estimation_data[code]
            item['name'] = est['name']  # 更新基金名称
            item['estimated_value'] = est['estimated_value']
            item['estimated_change_rate'] = est['estimated_change_rate']
            item['unit_net_value'] = est['unit_net_value']
        
        # 计算收益
        profit_info = calculate_profit(item)
        item.update(profit_info)
        
        result.append(item)
    
    cache_data = result
    cache_timestamp = datetime.now()
    print(f"✅ 缓存已更新 ({len(result)} 个基金)")


def background_updater():
    """后台定时更新线程"""
    while True:
        try:
            update_cache()
            time.sleep(60)  # 每60秒更新一次
        except Exception as e:
            print(f"❌ 后台更新失败: {e}")
            time.sleep(60)


# ==================== API 路由 ====================

@app.route('/api/funds', methods=['GET'])
def get_funds():
    """获取所有基金持仓和估值"""
    return jsonify({
        'success': True,
        'data': cache_data,
        'update_time': cache_timestamp.strftime('%Y-%m-%d %H:%M:%S') if cache_timestamp else None,
        'total_count': len(cache_data)
    })


@app.route('/api/funds/add', methods=['POST'])
def add_fund():
    """添加基金持仓"""
    data = request.json
    
    # 验证必填字段
    required_fields = ['code', 'cost_price', 'shares']
    for field in required_fields:
        if field not in data:
            return jsonify({
                'success': False,
                'message': f'缺少必填字段: {field}'
            }), 400
    
    # 检查是否已存在
    if any(h['code'] == data['code'] for h in fund_holdings):
        return jsonify({
            'success': False,
            'message': '该基金已存在，请勿重复添加'
        }), 400
    
    # 生成ID
    new_id = max([h['id'] for h in fund_holdings], default=0) + 1
    
    # 添加持仓
    new_holding = {
        'id': new_id,
        'code': data['code'],
        'name': data.get('name', ''),
        'cost_price': float(data['cost_price']),
        'shares': float(data['shares']),
        'add_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    fund_holdings.append(new_holding)
    save_holdings()
    
    # 立即更新缓存
    update_cache()
    
    return jsonify({
        'success': True,
        'message': '添加成功',
        'data': new_holding
    })


@app.route('/api/funds/delete/<int:fund_id>', methods=['DELETE'])
def delete_fund(fund_id):
    """删除基金持仓"""
    global fund_holdings
    
    original_length = len(fund_holdings)
    fund_holdings = [h for h in fund_holdings if h['id'] != fund_id]
    
    if len(fund_holdings) == original_length:
        return jsonify({
            'success': False,
            'message': '基金不存在'
        }), 404
    
    save_holdings()
    update_cache()
    
    return jsonify({
        'success': True,
        'message': '删除成功'
    })


@app.route('/api/funds/update/<int:fund_id>', methods=['PUT'])
def update_fund(fund_id):
    """更新基金持仓信息"""
    data = request.json
    
    # 查找基金
    holding = next((h for h in fund_holdings if h['id'] == fund_id), None)
    if not holding:
        return jsonify({
            'success': False,
            'message': '基金不存在'
        }), 404
    
    # 更新字段
    if 'cost_price' in data:
        holding['cost_price'] = float(data['cost_price'])
    if 'shares' in data:
        holding['shares'] = float(data['shares'])
    if 'name' in data:
        holding['name'] = data['name']
    
    save_holdings()
    update_cache()
    
    return jsonify({
        'success': True,
        'message': '更新成功',
        'data': holding
    })


@app.route('/api/summary', methods=['GET'])
def get_summary():
    """获取汇总信息"""
    if not cache_data:
        return jsonify({
            'success': True,
            'data': {
                'total_cost': 0,
                'total_value': 0,
                'total_profit': 0,
                'total_profit_rate': 0,
                'fund_count': 0
            }
        })
    
    total_cost = sum(item['total_cost'] for item in cache_data)
    total_value = sum(item['current_value'] for item in cache_data if item['current_value'] is not None)
    total_profit = total_value - total_cost
    total_profit_rate = (total_profit / total_cost * 100) if total_cost > 0 else 0
    
    return jsonify({
        'success': True,
        'data': {
            'total_cost': round(total_cost, 2),
            'total_value': round(total_value, 2),
            'total_profit': round(total_profit, 2),
            'total_profit_rate': round(total_profit_rate, 2),
            'fund_count': len(cache_data)
        }
    })


@app.route('/api/refresh', methods=['POST'])
def refresh_data():
    """手动刷新数据"""
    update_cache()
    return jsonify({
        'success': True,
        'message': '刷新成功'
    })


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })


if __name__ == '__main__':
    print("=" * 60)
    print("🚀 基金实时估值服务启动中...")
    print("=" * 60)
    
    # 加载历史数据
    load_holdings()
    
    # 初始化缓存
    print("📊 正在初始化缓存数据...")
    update_cache()
    
    # 启动后台更新线程
    print("⏰ 启动后台自动更新线程（每60秒）...")
    updater_thread = threading.Thread(target=background_updater, daemon=True)
    updater_thread.start()
    
    print("=" * 60)
    print("✅ 服务已启动！")
    print("📍 API地址: http://localhost:5000")
    print("📖 API文档:")
    print("   - GET  /api/funds       获取所有基金")
    print("   - POST /api/funds/add   添加基金")
    print("   - PUT  /api/funds/update/<id>  更新基金")
    print("   - DELETE /api/funds/delete/<id> 删除基金")
    print("   - GET  /api/summary     获取汇总")
    print("   - POST /api/refresh     手动刷新")
    print("=" * 60)
    
    # 启动Flask服务
    app.run(host='0.0.0.0', port=8899, debug=False)
