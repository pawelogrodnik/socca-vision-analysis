import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { IdentityCandidateAdminSection as ExtraAdmin } from './IdentityCandidateAdminSection';
import './styles.css';

const Root = () => (
  <React.StrictMode>
    <App />
    <ExtraAdmin />
  </React.StrictMode>
);

ReactDOM.createRoot(document.getElementById('root')!).render(<Root />);
