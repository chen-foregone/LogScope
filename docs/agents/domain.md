# Domain Docs

采用 single-context：根目录 GLOSSARY.md 与 docs/adr/。

探索代码前，阅读词汇表及与当前任务相关的 ADR。
文件不存在时直接继续，不预先创建，也不提示补建；
由 domain-modeling 在术语或决策明确后按需生成。

若将来出现 GLOSSARY-MAP.md，则按其索引阅读相关上下文词汇表，
并检查 src/<context>/docs/adr/ 中的相关决策。

工单、方案、假设与测试名称应使用词汇表定义的术语。
术语缺失时先判断是否误用概念，真实缺口留给 domain-modeling。
方案若与现有 ADR 冲突，须明确指出相关 ADR 和重新讨论的理由。
