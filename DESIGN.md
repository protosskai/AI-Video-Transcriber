---
name: Liusheng Transcription
colors:
  primary: "#292823"
  secondary: "#77756C"
  accent: "#B65B3D"
  surface: "#FFFEFA"
  background: "#FAF9F5"
  error: "#A33D34"
typography:
  body:
    fontFamily: "system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "16px"
    lineHeight: "1.8"
rounded:
  sm: "8px"
  md: "14px"
spacing:
  sm: "8px"
  md: "16px"
  lg: "24px"
---

## Overview

核心产品是“把媒体网站的音视频转成文字”，不是内容管理系统。用户的第一动作是贴链接；历史记录帮助用户找回结果，不与主任务争夺注意力。保留 google-labs-code/design.md 的设计意图记录方法。

视觉参考 [Claude 官网](https://claude.com/product/overview) 的暖白、炭黑、陶土色与人文排版；不复制品牌标识、不宣称关联。交互借鉴 [NN/g 渐进呈现](https://www.nngroup.com/articles/progressive-disclosure/)：高频动作直接可见，低频操作放在清晰的次级入口。参考原则服务于本产品，不把聊天框或营销页结构机械套过来。

## Colors

暖纸色背景、较深侧栏、陶土色主按钮。正文以炭黑为主，不用颜色堆砌模块。浅深主题沿用相同层级。

## Typography

标题采用本机宋体/衬线字体，正文和界面使用系统无衬线字体，不依赖外部字体 CDN。正文 17px、1.9 行高、最大 820px。标题下不强制添加解释性小字。正文第一标题与页面标题相同时只显示一次。

## Layout

首页直接呈现链接输入框和“开始转录”，上传是并列替代入口，语言/缓存收进选项。左侧最近转录为次级导航。结果页默认即为阅读布局，不设置“专注阅读”模式。手机侧栏由菜单打开，关闭回到原位置。

## Elevation & Depth

留白与细分隔线表达层级。阴影限于输入框、浮层。复制在原按钮反馈并自动恢复；一般成功 toast 2.6 秒自动消失、不移动布局；错误保持在相关操作附近，不用短暂 toast 掩盖失败。

## Shapes

按钮与输入 8px，面板与对话框 14px。避免把每行内容都包装成卡片。

## Components

文稿旁仅保留类型切换、复制、下载；复制链接、重命名、归档放入“更多”。重命名原位编辑，Enter 保存、Escape 取消、错误保留输入；不使用 window.prompt/confirm。涉及重新调用模型、移入回收站时用样式一致的对话框。失败时就地给出重试，日志折叠。原文默认优先，不把 AI 整理稿暗示成逐字记录。

## Do's and Don'ts

保持阅读位置、筛选、选中的执行版本。不得在轮询心跳时重建正文；不要用随机进度或假完成。危险操作确认且可恢复。所有文本作为不可信输入渲染，禁用原始 HTML。优先语义 HTML 与原生控件；遵循 reduced-motion。

- 不用“新建内容”代替用户熟悉的“转录”。
- 不给每个区块加解释副标题、英文眉题或系统架构说明。
- 不要求用户关闭成功提示；不把所有功能摆成一排按钮。
- 不因简化界面删除任务持久化、历史成果、错误恢复和下载保护。
