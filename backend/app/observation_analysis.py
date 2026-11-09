"""Ground V2 output in retained facts; model classification is only a candidate."""

CATEGORIES = {'dependency': '依赖异常', 'resource': '资源问题', 'configuration': '配置问题',
              'permission': '权限问题', 'application': '应用逻辑', 'unknown': '未知原因'}
COMPONENTS = {'gateway': '网关', 'application': '订单应用', 'database': '数据库', 'redis': 'Redis'}
SIGNALS = {'request_timeout': '报告请求超时', 'dependency_timeout': '报告依赖超时',
           'configuration_missing': '报告配置缺失', 'permission_denied': '报告权限拒绝'}
OBSERVED_CATEGORIES = {'pool_capacity': 'resource', 'configuration_missing': 'configuration',
                       'permission_denied': 'permission', 'dependency_timeout': 'dependency'}
READONLY_RECOMMENDATIONS = ('在当前调查范围内只读核查候选方向的日志和现有指标',
                            '核查传播依据和关键矛盾；扩大范围须由人工另开调查')
MODEL_CONTEXT_VERSION = 'bounded-observations-v1'


def model_evidence_context(materials):
    """Project retained V2 facts without identity distractions or inferred causal links."""
    projected = []
    directions = {}
    for item in materials:
        snippet = item.get('snippet') or {}
        observation = snippet.get('observation')
        if not observation:
            projected.append(item)
            continue
        category = OBSERVED_CATEGORIES.get(observation['signal'])
        if category:
            directions.setdefault(category, []).append(item['id'])
        fields = snippet.get('fields') or {}
        limits = {'business_impact': 'not_observed', 'cross_component_causality': 'not_established'}
        if snippet.get('causal_trace'):
            limits.update(named_dependency='not_observed', trace_scope='this_event_only')
        projected.append({**item, 'snippet': {**snippet,
            'fields': {key: value for key, value in fields.items() if key == 'correlation_id'}},
            'interpretation_limits': limits})
    result = {'evidence': projected}
    if any((item.get('snippet') or {}).get('observation') for item in materials):
        result.update(context_protocol=MODEL_CONTEXT_VERSION,
            observed_directions=[{'category': category, 'evidence_ids': ids}
                                 for category, ids in directions.items()],
            observation_category=next(iter(directions)) if len(directions) == 1 else 'unknown',
            summary_boundary='Server-derived observation labels, not root causes. Multiple directions '
                             'must remain separate. Neither shared nor different correlation IDs '
                             'establish causal propagation or independent causes. Empty or unavailable '
                             'sources cannot exclude faults. Trace declarations apply only to their event.')
    return result


def ground_observations(result, materials, *, required=False):
    observations = [(item.get('snippet') or {}).get('observation') for item in materials]
    if not required and not any(observations):
        return
    directions = {}
    for evidence_id, observation in zip(result['evidence_ids'], observations):
        category = OBSERVED_CATEGORIES.get(observation['signal']) if observation else None
        if category:
            directions.setdefault(category, []).append(evidence_id)
    # Classify the reported observation, never an inferred cause. Keep model disagreement auditable.
    result['model_category'] = result['category']
    result['observed_directions'] = [{'category': category, 'evidence_ids': ids}
                                     for category, ids in directions.items()]
    result['category'] = next(iter(directions)) if len(directions) == 1 else 'unknown'
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
        causal_chain=description + '。观测方向：' + CATEGORIES[result['category']]
                     + '；模型候选方向：' + CATEGORIES[result['model_category']]
                     + '，尚未验证。观测分类不证明最初故障原因或跨组件因果关系。' + trace_note,
        alternatives='现有材料未排除网络、应用逻辑、资源、配置及依赖异常；不能从未返回日志排除任何方向。',
        contradictions='尚未完成全部替代解释和矛盾核查；没有报告反证不等于已排除其他原因。',
        missing=['同一调用的可核验因果关系及具体依赖身份', '候选故障机制、业务影响和替代解释核验材料'],
        recommendations=list(READONLY_RECOMMENDATIONS),
        boundary='真实模型仅提供候选分类与证据选择；解释按受控结构化观测生成，未采纳自由文本因果断言和修改建议。模拟来源，非生产根因确认。')
