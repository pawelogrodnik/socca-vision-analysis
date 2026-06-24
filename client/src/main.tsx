import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { IdentityCandidateAdminSection } from './IdentityCandidateAdminSection';
import './styles.css';

const root = ReactDOM.createRoot(document.getElementById('root')!);

root.render(
  <React.StrictMode>
    <App />
    <IdentityCandidateAdminSection />
  </React.StrictMode>,
);
