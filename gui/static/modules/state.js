"use strict";

/* ── 跨模块共享的可变状态 ──
   拆模块前它们是 app.js 顶部的模块级变量；现在集中放这里，各模块 `import { S }` 后用 S.xxx 读写。
   不导出裸变量当可变绑定：ES 模块的 import 绑定对导入方只读，赋值会直接抛 TypeError。 */
export const S = {
sel: new Set(),                  // 已选文件路径集合
curDir: "",
rootDir: "",
treeData: [],                    // 当前目录的 entries（含 children）
flatList: [],                    // 展平后的可见行 [{row, entry, path, depth, expanded, parentEntry}]
focusIdx: -1,                    // 键盘焦点索引
};
