import { Routes, Route, Navigate } from 'react-router-dom'
import Sidebar from '@/components/Sidebar'
import Accounts from '@/pages/Accounts'
import Pipeline from '@/pages/Pipeline'

/**
 * 占位页(Phase 2 只实现账号管理,其他 5 屏 Phase 3+ 实现)。
 * 对照 Design-Brief:空状态用图标 + 极简文案 + 下一步引导。
 */
function Placeholder({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="p-6 max-w-[1400px]">
      <h1 className="text-lg font-semibold mb-6">{title}</h1>
      <div className="text-center py-16 text-text-secondary">
        <div className="text-sm">{hint}</div>
        <div className="text-xs text-text-tertiary mt-2">Phase 3+ 实现</div>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <div className="grid min-h-screen" style={{ gridTemplateColumns: '220px 1fr' }}>
      <Sidebar />
      <main>
        <Routes>
          <Route path="/" element={<Placeholder title="仪表盘" hint="账号状态总览与今日任务" />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="/accounts" element={<Accounts />} />
          <Route path="/comments" element={<Placeholder title="评论中心" hint="各账号评论与 AI 回复记录" />} />
          <Route path="/data" element={<Placeholder title="数据概览" hint="基础数据回显" />} />
          <Route path="/settings" element={<Placeholder title="日志与设置" hint="任务日志、AI/风控配置" />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}
