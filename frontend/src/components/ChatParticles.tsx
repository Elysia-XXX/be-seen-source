import {useEffect,useRef,useState} from 'react'
import {Pause,Play} from 'lucide-react'

// Decorative shapes only: these particles never represent report evidence.
export default function ChatParticles(){
 const canvas=useRef<HTMLCanvasElement>(null),time=useRef(0)
 const [paused,setPaused]=useState(()=>matchMedia('(prefers-reduced-motion: reduce)').matches)
 useEffect(()=>{
  const media=matchMedia('(prefers-reduced-motion: reduce)')
  const change=()=>setPaused(media.matches)
  media.addEventListener('change',change)
  return()=>media.removeEventListener('change',change)
 },[])
 useEffect(()=>{
  const el=canvas.current,ctx=el?.getContext('2d');if(!el||!ctx)return
  let w=0,h=0,frame=0,last=0
  const count=innerWidth<760?900:2000
  const shape=(kind:number,i:number,t:number)=>{
   const u=i/count,v=((i*0.61803398875)%1),a=u*Math.PI*2
   switch(kind){
    case 0:return [(u-.5)*2.8,Math.sin(u*9+t*.16)*.24+(v-.5)*.6,Math.cos(u*7+v*4)*.3]
    case 1:return [Math.cos(a*3+(i%2)*Math.PI)*.48,(u-.5)*2.1,Math.sin(a*3+(i%2)*Math.PI)*.48]
    case 2:{const r=.35+.58*Math.cos(a*5);return [Math.cos(a)*r,Math.sin(a)*r,(v-.5)*.32]}
    case 3:{const edge=i%4,s=v*2-1;return [edge<2?s:(edge===2?1:-1)*(1-Math.abs(s)),edge<2?(edge===0?1:-1)*(1-Math.abs(s)):s,Math.sin(a*4)*.23]}
    case 4:{const r=.3+v*.65;return [Math.sin(a)*r*1.5,Math.sin(a*2)*r*.65,Math.cos(a)*.35]}
    case 5:{const r=.2+u*.85,b=a*3+t*.06;return [Math.cos(b)*r,Math.sin(b)*r*.8,(v-.5)*.45]}
    default:{const x=(u-.5)*2.5,y=(v-.5)*1.5;return [x,y,Math.sin(x*3+t*.12)*Math.cos(y*3)*.55]}
   }
  }
  const draw=()=>{
   ctx.clearRect(0,0,w,h)
   const t=time.current,phase=t/9,k=Math.floor(phase)%7,f=phase%1
   const blend=Math.max(0,Math.min(1,(f-.5)/.5)),ease=blend*blend*(3-2*blend)
   const scale=Math.min(w*.43,h*.44),rot=Math.sin(t*.07)*.28
   el.dataset.pattern=['wave','helix','petals','diamond','infinity','spiral','ripple'][k]
   for(let i=0;i<count;i++){
    const a=shape(k,i,t),b=shape((k+1)%7,i,t)
    const x=a[0]+(b[0]-a[0])*ease,y=a[1]+(b[1]-a[1])*ease,z=a[2]+(b[2]-a[2])*ease
    const depth=1+z*.22,px=w*.53+(x*Math.cos(rot)-z*Math.sin(rot))*scale*depth,py=h*.43+y*scale*depth
    const gold=i%5===0,alpha=.48+(z+.5)*.32
    ctx.fillStyle=gold?`rgba(231,199,120,${alpha})`:`rgba(113,203,255,${alpha})`
    const r=(i%19===0?2.4:1.25)*depth
    ctx.fillRect(px,py,r,r)
    if(i%13===0){
     ctx.fillStyle=gold?'rgba(247,210,129,.09)':'rgba(113,203,255,.09)'
     ctx.beginPath();ctx.arc(px+r/2,py+r/2,r*3.5,0,Math.PI*2);ctx.fill()
    }
   }
  }
  const resize=()=>{const box=el.getBoundingClientRect();w=box.width;h=box.height;const d=Math.min(devicePixelRatio,1.5);el.width=w*d;el.height=h*d;ctx.setTransform(d,0,0,d,0,0);draw()}
  const tick=(now:number)=>{if(now-last>32){if(!document.hidden){time.current+=Math.min((now-last)/1000,.06);draw()}last=now}frame=requestAnimationFrame(tick)}
  const observer=new ResizeObserver(resize);observer.observe(el);resize()
  if(!paused)frame=requestAnimationFrame(tick)
  return()=>{cancelAnimationFrame(frame);observer.disconnect()}
 },[paused])
 return <><div className="chat-atmosphere" aria-hidden="true"><canvas ref={canvas}/></div><button className="particle-toggle icon-button" aria-label={paused?'播放背景粒子':'暂停背景粒子'} aria-pressed={paused} onClick={()=>setPaused(!paused)}>{paused?<Play size={14}/>:<Pause size={14}/>}</button></>
}
