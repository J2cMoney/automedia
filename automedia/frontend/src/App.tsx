import { Routes, Route, Navigate } from 'react-router-dom'
import Sidebar from '@/components/Sidebar'
import Accounts from '@/pages/Accounts'
import Pipeline from '@/pages/Pipeline'
import Dashboard from '@/pages/Dashboard'
import Comments from '@/pages/Comments'
import DataOverview from '@/pages/DataOverview'
import Settings from '@/pages/Settings'

/**
 * 路由 - Phase 6 全部 6 屏接入。
 *
 * 对照 Design-Brief §2.1 导航结构:
 *   /          仪表盘(SCREEN-1)
 *   /pipeline  内容流水线(SCREEN-3)
 *   /accounts  账号管理(SCREEN-2)
 *   /comments  评论中心(SCREEN-4)
 *   /data      数据概览(SCREEN-5)
 *   /settings  日志与设置(SCREEN-6)
 */
export default function App() {
  return (
    <div className="grid min-h-screen" style={{ gridTemplateColumns: '220px 1fr' }}>
      <Sidebar />
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="/accounts" element={<Accounts />} />
          <Route path="/comments" element={<Comments />} />
          <Route path="/data" element={<DataOverview />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}
