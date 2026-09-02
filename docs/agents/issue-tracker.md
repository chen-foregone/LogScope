# Issue tracker: Local Markdown

- 每项功能使用 `.scratch/<feature-slug>/` 目录。
- 规格文档为该目录下的 `spec.md`。
- 每张实施工单独立保存到 `issues/<NN>-<slug>.md`，从 01 编号。
- 工单顶部通过 `Status:` 记录分诊状态，取值见 `triage-labels.md`。
- 评论与讨论追加到文件底部的 `## Comments`。
- 发布事项时按上述路径创建文件；获取工单时读取指定文件。

## Wayfinding operations
- 工作地图：`.scratch/<effort>/map.md`，包含 Notes、Decisions-so-far、Fog。
- 子工单：`issues/NN-<slug>.md`，从 01 编号，正文记录待解问题。
- `Type:` 为 research、prototype、grilling 或 task。
- 阻塞关系通过 `Blocked by: NN, NN` 记录；所列工单全部 resolved 后解除。
- 优先处理编号最小、未解决、无阻塞且未认领的工单。
- 开始前保存 `Status: claimed`。
- 完成后追加 `## Answer`，设为 `Status: resolved`，
  并将结论摘要和工单链接追加到地图的 Decisions-so-far。
