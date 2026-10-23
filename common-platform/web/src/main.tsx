import React from 'react';
import ReactDOM from 'react-dom/client';
import SiteRoot from './SiteRoot';
import ErrorBoundary from './ErrorBoundary';
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><ErrorBoundary><SiteRoot/></ErrorBoundary></React.StrictMode>);
