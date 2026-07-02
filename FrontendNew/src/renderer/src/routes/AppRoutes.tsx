import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from '../components/AppLayout/AppLayout'
import AgentsPage from '../pages/AgentsPage/AgentsPage'
import FilePage from '../pages/FilePage/FilePage'
import NewTaskPage from '../pages/NewTaskPage/NewTaskPage'
import PlaceholderPage from '../pages/PlaceholderPage/PlaceholderPage'
import SkillPage from '../pages/SkillPage/SkillPage'
import TaskDetailPage from '../pages/TaskDetailPage/TaskDetailPage'

function AppRoutes(): React.JSX.Element {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route element={<Navigate replace to="/skill" />} index />
        <Route element={<SkillPage />} path="skill" />
        <Route element={<TaskDetailPage />} path="task/:taskId" />
        <Route element={<NewTaskPage />} path="new-task" />
        <Route element={<AgentsPage />} path="agents" />
        <Route element={<FilePage />} path="files" />
        <Route element={<PlaceholderPage title="SubAgent" />} path="sub-agent" />
        <Route element={<PlaceholderPage title="搜索任务" />} path="search" />
        <Route element={<Navigate replace to="/skill" />} path="*" />
      </Route>
    </Routes>
  )
}

export default AppRoutes
