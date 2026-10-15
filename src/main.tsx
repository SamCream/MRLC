import React from 'react';
import { createRoot } from 'react-dom/client';
import AuthGate from './app/AuthGate';
import './styles/original.css';

createRoot(document.getElementById('root')!).render(<AuthGate />);
