import {PresentationFile,FileBlob} from '@oai/artifact-tool';import fs from 'node:fs/promises';import path from 'node:path';import {fileURLToPath} from 'node:url';
const B=path.dirname(fileURLToPath(import.meta.url)),W=path.dirname(B);
await fs.copyFile(path.join(B,'final/deck.pptx'),path.join(W,'3DIC_EDA_算法迭代汇报.pptx'),1);
const p=await PresentationFile.importPptx(await FileBlob.load(path.join(W,'3DIC_EDA_算法迭代汇报.pptx')));
for(let i=0;i<p.slides.items.length;i++){
 const b=await p.export({slide:p.slides.items[i],format:'png',scale:1.5});
 await fs.writeFile(path.join(W,'preview',`slide-${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await b.arrayBuffer()));console.log('final',i+1);
}
