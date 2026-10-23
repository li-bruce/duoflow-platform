/** Incremental SHA-256: files are hashed in bounded slices, never read whole. */
const K=new Uint32Array([0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
const rotate=(x:number,n:number)=>(x>>>n)|(x<<(32-n));
export class Sha256 {
  private state=new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
  private buffer=new Uint8Array(64);private buffered=0;private bytes=0;private done=false;
  private block(bytes:Uint8Array,offset:number){
    const w=new Uint32Array(64);
    for(let i=0;i<16;i++){const j=offset+i*4;w[i]=(bytes[j]<<24)|(bytes[j+1]<<16)|(bytes[j+2]<<8)|bytes[j+3];}
    for(let i=16;i<64;i++){const x=w[i-15],y=w[i-2];w[i]=w[i-16]+(rotate(x,7)^rotate(x,18)^(x>>>3))+w[i-7]+(rotate(y,17)^rotate(y,19)^(y>>>10));}
    let [a,b,c,d,e,f,g,h]=this.state;
    for(let i=0;i<64;i++){const t1=(h+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+K[i]+w[i])>>>0;const t2=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))>>>0;h=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;}
    const value=[a,b,c,d,e,f,g,h];for(let i=0;i<8;i++)this.state[i]+=value[i];
  }
  update(bytes:Uint8Array){
    if(this.done)throw new Error('摘要已经完成。');this.bytes+=bytes.length;let offset=0;
    if(this.buffered){const n=Math.min(64-this.buffered,bytes.length);this.buffer.set(bytes.subarray(0,n),this.buffered);this.buffered+=n;offset=n;if(this.buffered===64){this.block(this.buffer,0);this.buffered=0;}}
    while(offset+64<=bytes.length){this.block(bytes,offset);offset+=64;}
    if(offset<bytes.length){this.buffer.set(bytes.subarray(offset),0);this.buffered=bytes.length-offset;}
    return this;
  }
  hex(){
    if(this.done)throw new Error('摘要已经完成。');this.done=true;
    const last=new Uint8Array(this.buffered<56?64:128);last.set(this.buffer.subarray(0,this.buffered));last[this.buffered]=0x80;
    const bits=BigInt(this.bytes)*8n;const view=new DataView(last.buffer);view.setUint32(last.length-8,Number(bits>>32n));view.setUint32(last.length-4,Number(bits&0xffffffffn));
    for(let i=0;i<last.length;i+=64)this.block(last,i);
    return Array.from(this.state,n=>n.toString(16).padStart(8,'0')).join('');
  }
}
export async function fileSha256(file:Blob){const hash=new Sha256();for(let offset=0;offset<file.size;offset+=1024*1024)hash.update(new Uint8Array(await file.slice(offset,offset+1024*1024).arrayBuffer()));return hash.hex();}
