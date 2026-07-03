/**
 * Remotion 入口文件 - registerRoot。
 *
 * Python 渲染命令指向这个文件:
 *   npx remotion render src/entry.ts Main out.mp4 --props=...
 */
import { registerRoot } from "remotion";
import { RemotionRoot } from "./index";

registerRoot(RemotionRoot);
