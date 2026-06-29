import { Navigate, Route, Routes } from 'react-router-dom';
import { AdminPanel } from './components/AdminPanel';
import { MatchReportPage } from './components/MatchReportPage';
import { PlayerProfilePage } from './components/PlayerProfilePage';
import { PublishedMatchReportPage } from './components/PublishedMatchReportPage';
import { TeamEditPage } from './components/TeamEditPage';
import { TeamsPage } from './components/TeamsPage';
import { Viewer } from './components/Viewer';

function App() {
  return (
    <Routes>
      <Route path='/' element={<Viewer />} />
      <Route path='/admin-panel' element={<AdminPanel />} />
      <Route path='/matches/:matchId/report' element={<MatchReportPage />} />
      <Route path='/published/matches/:matchId/report' element={<PublishedMatchReportPage />} />
      <Route path='/teams' element={<TeamsPage />} />
      <Route path='/teams/add' element={<TeamEditPage mode='create' />} />
      <Route path='/teams/:teamId' element={<TeamEditPage mode='edit' />} />
      <Route path='/players/:playerId' element={<PlayerProfilePage />} />
      <Route path='*' element={<Navigate to='/' replace />} />
    </Routes>
  );
}

export default App;
