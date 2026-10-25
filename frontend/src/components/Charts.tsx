import {useEffect,useRef} from 'react'
import * as echarts from 'echarts/core'
import {BarChart,HeatmapChart} from 'echarts/charts'
import {GridComponent,TooltipComponent,VisualMapComponent} from 'echarts/components'
import {CanvasRenderer} from 'echarts/renderers'
import type {ChatReport} from '../chat-types'
echarts.use([BarChart,HeatmapChart,GridComponent,TooltipComponent,VisualMapComponent,CanvasRenderer])
export default function Charts({report,onScene}:{report:ChatReport;onScene:(id:string)=>void}){
 const matrix=useRef<HTMLDivElement>(null),bars=useRef<HTMLDivElement>(null)
 useEffect(()=>{
  if(!matrix.current||!bars.current)return
  const chart=echarts.init(matrix.current),bar=echarts.init(bars.current)
  const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches
  chart.setOption({animation:!reduced,backgroundColor:'transparent',tooltip:{trigger:'item',renderMode:'richText',formatter:(p:{data:number[]})=>{const s=report.scenarios[p.data[0]];return s.question+'\n'+(['未完成','未发现','待辨认','确认发现'][p.data[2]])}},grid:{top:12,bottom:45,left:10,right:10},xAxis:{type:'category',data:report.scenarios.map((_,i)=>String(i+1)),axisLabel:{color:'#b5c5d8',interval:0},axisLine:{show:false}},yAxis:{type:'category',data:['搜索发现'],show:false},visualMap:{show:false,min:0,max:3,inRange:{color:['#28384a','#3c4653','#c39859','#62dfe8']}},series:[{type:'heatmap',data:report.scenarios.map((s,i)=>[i,0,!s.search_ok?0:s.match==='confirmed'?3:s.match==='suspected'?2:1]),itemStyle:{borderWidth:4,borderColor:'#1b2a3d'},label:{show:false}}]})
  chart.on('click',p=>{if(typeof p.dataIndex==='number')onScene(report.scenarios[p.dataIndex].id)})
  bar.setOption({animation:!reduced,backgroundColor:'transparent',grid:{top:15,left:110,right:40,bottom:30},xAxis:{type:'value',minInterval:1,axisLabel:{color:'#b5c5d8'},splitLine:{lineStyle:{color:'#34485f'}}},yAxis:{type:'category',data:report.competitors.map(c=>c.name),axisLabel:{color:'#f4f6fa',width:95,overflow:'truncate'},axisLine:{show:false},axisTick:{show:false}},series:[{type:'bar',data:report.competitors.map(c=>c.scenes.length),barMaxWidth:18,itemStyle:{color:'#62dfe8',borderRadius:[0,3,3,0]},label:{show:true,position:'right',color:'#f4f6fa'}}]})
  const resize=new ResizeObserver(()=>{chart.resize();bar.resize()});resize.observe(matrix.current);resize.observe(bars.current)
  return()=>{resize.disconnect();chart.dispose();bar.dispose()}
 },[report,onScene])
 return <section className="chart-section"><h3>需求场景中的发现</h3><p className="muted">每格对应一个场景，点击查看原始证据。灰色未发现，琥珀色待辨认，青色确认发现。</p><div ref={matrix} style={{height:105}}/><h3>同批结果中的竞品</h3><p className="muted">包含商家的场景数，不代表商家排名。</p>{!report.competitors.length&&<p>未找到身份明确的同城竞品。</p>}<div ref={bars} style={{height:160}}/></section>
}
