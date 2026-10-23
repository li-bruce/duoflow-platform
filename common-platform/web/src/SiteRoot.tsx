import { lazy, Suspense, useEffect, useState } from 'react';
import FrameworkGuide from './guide/FrameworkGuide';

const App = lazy(() => import('./App'));
const DeveloperGuide = lazy(() => import('./developer-guide/DeveloperGuide'));
const currentRoute = () => window.location.hash === '#developer-guide' ? 'developer' : ['', '#', '#guide'].includes(window.location.hash) ? 'guide' : 'workspace';

export default function SiteRoot() {
  const [route, setRoute] = useState(currentRoute);
  useEffect(() => {
    const onHash = () => setRoute(currentRoute());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => { document.title = route === 'guide' ? '一步一步，认识这个框架' : route === 'developer' ? '框架全貌 · 构造与交互规则' : '共用能力工作台'; }, [route]);
  return route === 'guide' ? <FrameworkGuide/> : <Suspense fallback={<div className="boot">正在打开{route === 'developer' ? '框架全貌' : '工作台'}…</div>}>{route === 'developer' ? <DeveloperGuide/> : <App/>}</Suspense>;
}
