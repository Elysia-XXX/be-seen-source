import {useEffect, useRef, useState} from 'react'
import ForceGraph3D, {type ForceGraph3DInstance} from '3d-force-graph'
import {UnrealBloomPass} from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import type {OrbitControls} from 'three/examples/jsm/controls/OrbitControls.js'
import {Vector2,CanvasTexture,Sprite,SpriteMaterial,Group} from 'three'
import {Focus, Pause, Play} from 'lucide-react'
import type {GraphData, GraphNode, GraphLink} from '../chat-types'

export const endpoint = (n: string | GraphNode) => typeof n === 'string' ? n : n.id
export default function Graph({data,focusedNode,onNode,onLink,onUnavailable,onClear}:{data:GraphData;focusedNode:GraphNode|null;onNode:(n:GraphNode)=>void;onLink:(l:GraphLink)=>void;onUnavailable:()=>void;onClear:()=>void}) {
  const host = useRef<HTMLDivElement>(null)
  const graph = useRef<ReturnType<typeof createGraph> | null>(null)
  const focusNode = useRef<(id:string)=>void>(()=>{})
  const expandGraph=useRef<(all:boolean)=>void>(()=>{})
  const [expanded,setExpanded]=useState(true)
  const [visibleCount,setVisibleCount]=useState(0)
  const marker=useRef<HTMLButtonElement>(null)
  const [focusedLabel,setFocusedLabel]=useState('')
  const callbacks=useRef({onNode,onLink,onUnavailable,onClear});callbacks.current={onNode,onLink,onUnavailable,onClear}
  const [paused,setPaused]=useState(matchMedia('(prefers-reduced-motion: reduce)').matches)
  const motion=useRef(!paused);motion.current=!paused
  function createGraph(el:HTMLElement){return new ForceGraph3D(el,{controlType:'orbit',rendererConfig:{antialias:true,alpha:false}}) as unknown as ForceGraph3DInstance<GraphNode,GraphLink>}
  useEffect(()=>{
    if(!host.current)return
    const el=host.current
    let g:ReturnType<typeof createGraph>
    try { g=createGraph(el) } catch {callbacks.current.onUnavailable();return}
    graph.current=g
    const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches
    let selected='',adjacent=new Set<string>(),fitted=false,showAll=true
    let visible=new Set(data.nodes.filter(n=>n.type==='query').map(n=>n.id))
    const visibilityForSelection=()=>{
      visible=new Set(data.nodes.filter(n=>n.type==='query').map(n=>n.id))
      if(selected){
        visible.add(selected)
        const neighbors=new Set(data.links.filter(l=>endpoint(l.source)===selected||endpoint(l.target)===selected).flatMap(l=>[endpoint(l.source),endpoint(l.target)]))
        neighbors.forEach(id=>visible.add(id))
        data.links.forEach(l=>{if(neighbors.has(endpoint(l.source))&&data.nodes.find(n=>n.id===endpoint(l.source))?.type!=='query'){visible.add(endpoint(l.source));visible.add(endpoint(l.target))}})
      }
      g.nodeVisibility(n=>showAll||visible.has(n.id)).linkVisibility(l=>showAll||visible.has(endpoint(l.source))&&visible.has(endpoint(l.target)))
      setVisibleCount(showAll?data.nodes.length:visible.size)
    }
    expandGraph.current=(all)=>{showAll=all;visibilityForSelection()}
    setExpanded(true)
    let focusFrame=0
    const updateMarker=()=>{
      const n=g.graphData().nodes.find(n=>n.id===selected)
      if(!marker.current)return
      if(!n){marker.current.hidden=true;return}
      const point=g.graph2ScreenCoords(n.x||0,n.y||0,n.z||0)
      marker.current.hidden=false
      marker.current.style.left=point.x+'px';marker.current.style.top=point.y+'px'
    }
    const textures:CanvasTexture[]=[],materials:SpriteMaterial[]=[]
    const colors=(n:GraphNode)=>n.type==='source'?'#62dfe8':n.type==='business'?'#f4f6fa':n.status==='suspected'||!n.search_ok?'#f0b86a':'#808b9e'
    const active=(l:GraphLink)=>!selected||endpoint(l.source)===selected||endpoint(l.target)===selected
    focusNode.current=(id:string)=>{
      if(!id){selected='';adjacent.clear();visibilityForSelection();setFocusedLabel('');updateMarker();g.nodeColor(g.nodeColor()).linkColor(g.linkColor()).linkWidth(g.linkWidth()).linkDirectionalParticles(g.linkDirectionalParticles());return}
      // The engine positions its own node copies. API nodes have no coordinates.
      const n=g.graphData().nodes.find(n=>n.id===id)
      if(!n)return
      selected=n.id;fitted=true
      visibilityForSelection()
      adjacent=new Set(data.links.filter(l=>endpoint(l.source)===n.id||endpoint(l.target)===n.id).flatMap(l=>[endpoint(l.source),endpoint(l.target)]))
      g.nodeColor(g.nodeColor()).linkColor(g.linkColor()).linkWidth(g.linkWidth()).linkDirectionalParticles(g.linkDirectionalParticles())
      const x=n.x||0,y=n.y||0,z=n.z||0
      const camera=g.cameraPosition(),dx=camera.x-x,dy=camera.y-y,dz=camera.z-z,distance=Math.hypot(dx,dy,dz)||1
      g.cameraPosition({x:x+dx/distance*110,y:y+dy/distance*110,z:z+(distance===1?110:dz/distance*110)},{x,y,z},reduced?0:850)
      setFocusedLabel(n.query||n.label)
      cancelAnimationFrame(focusFrame)
      const until=performance.now()+1200
      const track=()=>{updateMarker();if(performance.now()<until)focusFrame=requestAnimationFrame(track)}
      focusFrame=requestAnimationFrame(track)
    }
    g.backgroundColor('#000000').showNavInfo(false).nodeRelSize(3.5).nodeVal(1).nodeResolution(12)
      .nodeColor(n=>!selected||n.id===selected||adjacent.has(n.id)?colors(n):'#252b32')
      .nodeLabel(n=>{const label=document.createElement('div');label.className='graph-tooltip';label.textContent=n.label;return label})
      .nodeThreeObjectExtend(true).nodeThreeObject(n=>{
        const group=new Group()
        if(n.type==='source')return group
        const canvas=document.createElement('canvas');canvas.width=512;canvas.height=n.type==='query'?96:64
        const ctx=canvas.getContext('2d');if(!ctx)return group
        ctx.font='24px Microsoft YaHei, sans-serif';ctx.textAlign='center';ctx.fillStyle=n.type==='business'?'#c8dfe3':'#7e8d9f'
        const label=n.query||n.label
        ctx.fillText(label.slice(0,18)+(n.type!=='query'&&label.length>18?'…':''),256,35)
        if(n.type==='query'&&label.length>18)ctx.fillText(label.slice(18,36)+(label.length>36?'…':''),256,65)
        const texture=new CanvasTexture(canvas),material=new SpriteMaterial({map:texture,transparent:true,depthWrite:false})
        textures.push(texture);materials.push(material)
        const sprite=new Sprite(material);sprite.scale.set(58,n.type==='query'?10.875:7.25,1);sprite.position.set(0,-8,0);group.add(sprite);return group
      })
      .linkColor(l=>active(l)?l.relation==='模拟推荐'?'#f4f6fa':'#426c75':'#141a20')
      .linkOpacity(.55).linkWidth(l=>selected&&active(l)?1.5:.5)
      .linkDirectionalParticles(l=>motion.current&&!reduced&&selected&&active(l)?2:0).linkDirectionalParticleWidth(1.4)
      .onNodeClick(n=>{
        callbacks.current.onNode({...n})
      }).onLinkClick(l=>callbacks.current.onLink(l))
      .onBackgroundClick(()=>callbacks.current.onClear())
      .onEngineStop(()=>{if(!fitted){fitted=true;g.zoomToFit(reduced?0:750,95)}})
      .cooldownTicks(reduced?1:60).warmupTicks(120)
      .graphData({nodes:data.nodes.map(n=>({...n})),links:data.links.map(l=>({...l}))})
    visibilityForSelection()
    if(innerWidth>800&&!reduced){const bloom=new UnrealBloomPass(new Vector2(512,512),.65,.4,.2);g.postProcessingComposer().addPass(bloom)}
    g.renderer().setPixelRatio(Math.min(devicePixelRatio,1.5))
    const resize=()=>{g.width(el.clientWidth).height(el.clientHeight);updateMarker()}
    const observer=new ResizeObserver(resize);observer.observe(el);resize()
    const timer=setTimeout(()=>{if(!selected)g.zoomToFit(reduced?0:900,65)},400)
    const lost=(e:Event)=>{e.preventDefault();callbacks.current.onUnavailable()}
    g.renderer().domElement.addEventListener('webglcontextlost',lost)
    const visibility=()=>document.hidden?g.pauseAnimation():g.resumeAnimation()
    document.addEventListener('visibilitychange',visibility)
    const controls=g.controls() as OrbitControls
    controls.addEventListener('change',updateMarker)
    return()=>{cancelAnimationFrame(focusFrame);controls.removeEventListener('change',updateMarker);clearTimeout(timer);observer.disconnect();document.removeEventListener('visibilitychange',visibility);g.renderer().domElement.removeEventListener('webglcontextlost',lost);g._destructor();textures.forEach(t=>t.dispose());materials.forEach(m=>m.dispose());graph.current=null;focusNode.current=()=>{}}
  },[data])
  useEffect(()=>{focusNode.current(focusedNode?.id||'')},[focusedNode,data])
  useEffect(()=>{const g=graph.current;if(g)g.linkDirectionalParticles(g.linkDirectionalParticles())},[paused])
  return <div className="graph-stage"><div ref={host} className="graph-canvas" aria-label="证据关系网络，使用旁边的数据表可查看全部关系"/><button ref={marker} className="focused-node-marker" hidden aria-label={'查看已定位节点：'+focusedLabel} onClick={e=>{e.stopPropagation();if(focusedNode)callbacks.current.onNode({...focusedNode})}}/><span className="graph-focus-status" role="status">{focusedLabel?'已定位：'+focusedLabel:''}</span><div className="graph-controls"><button className="text-button" aria-pressed={expanded} onClick={()=>{expandGraph.current(!expanded);setExpanded(!expanded)}}>{expanded?'按查询收起':'展开全部关系'} · {visibleCount}/{data.nodes.length}</button><button className="icon-button" aria-label="适应画面" onClick={()=>{callbacks.current.onClear();graph.current?.zoomToFit(paused?0:650,65);setFocusedLabel('')}}><Focus size={19}/></button><button className="icon-button" aria-label={paused?'播放连线粒子':'暂停连线粒子'} onClick={()=>setPaused(!paused)}>{paused?<Play size={17}/>:<Pause size={17}/>}</button></div><div className="graph-legend"><span><i className="query-dot"/>查询</span><span><i className="source-dot"/>来源</span><span><i className="business-dot"/>商家</span><span><i className="uncertain-dot"/>待辨认 / 未完成</span></div></div>
}
