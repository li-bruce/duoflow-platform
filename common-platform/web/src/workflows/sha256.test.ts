import { describe, expect, it } from 'vitest';
import { Sha256 } from './sha256';
// Independently generated with Python hashlib; includes the padding and slice boundaries.
const expected:Record<number,string>={0:'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',3:'26320e0ae7028efb6bf710654a0ed53cfcae1e5ca1e7f69d8a9dce02cfac2960',55:'be6fa81b6dd5fba2459dfae9647b535b754b1e593fc5e538d4b092275f7d2d00',56:'9288dbcbf62343e076e614b71d1e584f3f0e1976134b86921bc79be3f414e6b7',63:'751eb0dee0145c6fd9645e58f26dedfbba223074973df31f931ecdba3830c3e1',64:'4331b5485c880a6a8b91828f37c23e3fe847adc55335a8edb14fef8531212eb3',65:'c5bbf822a70255c93801b952a12b007f6c032d6e1b11f95f5e6d47a1270af113',127:'4b7d696a8375f9eff3b6a3d0cebeffd1a470c07a468c084a1b699a52bb197607',128:'46349ee79d6bd8448ac91b598d4cf0a064d698a013049231c20e37a27e888f3a',1048605:'94aa0dc5906ca1f1c9d8bd9a6dd6ed48285d69f310e8f86112298d75f82a4ff4'};
describe('bounded incremental file hashing',()=>{
  for(const length of [0,3,55,56,63,64,65,127,128,1024*1024+29])it(`matches native SHA-256 for ${length} bytes across slice boundaries`,()=>{
    const bytes=new Uint8Array(length);for(let i=0;i<length;i++)bytes[i]=(i*17)%256;
    const hash=new Sha256();for(let i=0;i<length;i+=73)hash.update(bytes.subarray(i,i+73));
    expect(hash.hex()).toBe(expected[length]);
  });
});
