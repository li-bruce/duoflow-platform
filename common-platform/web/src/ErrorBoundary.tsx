import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';
export default class ErrorBoundary extends Component<{children:ReactNode;resetKey?:string},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  componentDidCatch(_error:Error,_info:ErrorInfo){/* Avoid logging data or credentials from a failed UI render. */}
  componentDidUpdate(previous:Readonly<{children:ReactNode;resetKey?:string}>){if(previous.resetKey!==this.props.resetKey&&this.state.failed)this.setState({failed:false});}
  render(){if(this.state.failed)return <section className="panel"><div className="notice error"><AlertTriangle size={18}/><div><strong>当前界面暂时无法显示</strong><p>已保存的数据不会因此改变。可以切换页面后再试，或重新载入工作台。</p></div></div><button className="button secondary" onClick={()=>location.reload()}><RotateCcw size={15}/>重新载入</button></section>;return this.props.children;}
}
