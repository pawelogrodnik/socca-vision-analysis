import { Navigate, Route, Routes } from 'react-router-dom';
import { AdminPanel } from './components/AdminPanel';
import { Viewer } from './components/Viewer';

function App() {
  return (
    <Routes>
      <Route path='/' element={<Viewer />} />
      <Route path='/admin-panel' element={<AdminPanel />} />
      <Route path='*' element={<Navigate to='/' replace />} />
    </Routes>
  );
}

export default App;
