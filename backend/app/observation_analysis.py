"""Ground V2 output in retained facts; model classification is only a candidate."""

CATEGORIES = {'dependency': '依赖异常', 'resource': '资源问题', 'configuration': '配置问题',
              'permission': '权限问题', 'application': '应用逻辑', 'unknown': '未知原因'}
COMPONENTS = {'gateway': '网关', 'application': '订单应用', 'database': '数据库', 'redis': 'Redis'}
SIGNALS = {'request_timeout': '报告请求超时', 'dependency_timeout': '报告依赖超时',
           'configuration_missing': '报告配置缺失', 'permission_denied': '报告权限拒绝'}


def ground_observations(result, materials, *, required=False):
    observations = [(item.get('snippet') or {}).get('observation') for item in materials]
    if not required and not any(observations):
        return
    facts = []
    for observation in observations:
        if observation is None:
            continue
        component = COMPONENTS[observation['component']]
        signal = observation['signal']
        if signal == 'http_response':
            fact = f"{component}报告HTTP {observation['http_status']}"
        elif signal == 'pool_capacity':
            fact = f"{component}连接池占用{observation['used']}/{observation['capacity']}个连接"
        else:
            fact = component + SIGNALS[signal]
        if fact not in facts:
            facts.append(fact)
    trace_note = ''
    if any((item.get('snippet') or {}).get('causal_trace') for item in materials):
        trace_note = '轨迹中的传播声明仅适用于其受控观测，未指明具体依赖身份；不能仅凭时间或代号绑定到某条依赖日志。'
    description = '；'.join(facts) if facts else '未引用本轮结构化观测，现有引用不足以支持故障解释'
    result.update(grounding='structured-observations-v1', phenomenon=description,
        causal_chain=description + '。模型候选方向：' + CATEGORIES[result['category']]
                     + '，尚未验证。上述观测不证明最初故障原因或跨组件因果关系。' + trace_note,
        alternatives='现有材料未排除网络、应用逻辑、资源、配置及依赖异常；不能从未返回日志排除任何方向。',
        contradictions='尚未完成全部替代解释和矛盾核查；没有报告反证不等于已排除其他原因。',
        missing=['同一调用的可核验因果关系及具体依赖身份', '候选故障机制、业务影响和替代解释核验材料'],
        recommendations=['在当前调查范围内只读核查候选方向的日志和现有指标',
                         '核查传播依据和关键矛盾；扩大范围须由人工另开调查'],
        boundary='真实模型仅提供候选分类与证据选择；解释按受控结构化观测生成，未采纳自由文本因果断言和修改建议。模拟来源，非生产根因确认。')
