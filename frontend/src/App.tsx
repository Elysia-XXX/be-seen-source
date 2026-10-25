import {lazy, Suspense, useState, useEffect} from 'react'
import {BrowserRouter, Routes, Route, Link} from 'react-router-dom'
import {ArrowUpRight, Aperture, KeyRound} from 'lucide-react'
import {api, post, token} from './api'
import Landing from './components/Landing'
import '@fontsource/manrope/latin-400.css'
import '@fontsource/manrope/latin-500.css'
import '@fontsource/manrope/latin-600.css'
import '@fontsource/manrope/latin-700.css'
const Chat = lazy(()=>import('./components/Chat'))
const Report = lazy(()=>import('./components/Report'))
export function Brand(){return <Link to="/" className="brand"><Aperture size={25} strokeWidth={1.4}/><span>被看见<span className="brand-en">Be seen</span></span></Link>}
function Gate({children}:{children:React.ReactNode}){
  const [authorized,setAuthorized]=useState(!!token()),[password,setPassword]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false)
  useEffect(()=>{const expired=()=>setAuthorized(false);window.addEventListener('auth-expired',expired);return()=>window.removeEventListener('auth-expired',expired)},[])
  if(authorized)return <>{children}</>
  return <main className="access"><Brand/><form onSubmit={async e=>{e.preventDefault();setBusy(true);try{const r=await api<{token:string}>('/auth/session',post({password}));sessionStorage.setItem('beseen-token',r.token);setAuthorized(true)}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}}><KeyRound size={28}/><h1>进入你的观察空间。</h1><p>输入演示口令，开始一次有据可查的探索。</p><label>访问口令<input type="password" value={password} onChange={e=>setPassword(e.target.value)} autoComplete="current-password" required autoFocus/></label>{error&&<p role="alert" className="error">{error}</p>}<button className="button primary" disabled={busy}>{busy?'正在进入':'进入对话'}<ArrowUpRight size={18}/></button></form></main>
}
export default function App(){return <BrowserRouter><Suspense fallback={<div className="page-loading"><Aperture className="spin"/><span>正在打开观察空间</span></div>}><Routes><Route path="/" element={<Landing/>}/><Route path="/chat/:id" element={<Gate><Chat/></Gate>}/><Route path="/reports/:id" element={<Gate><Report/></Gate>}/><Route path="*" element={<div className="empty"><h1>这里还没有内容</h1><Link to="/">返回首页</Link></div>}/></Routes></Suspense></BrowserRouter>}
