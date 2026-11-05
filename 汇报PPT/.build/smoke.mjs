import {Presentation,PresentationFile} from '@oai/artifact-tool';
import fs from 'node:fs/promises';
const p=Presentation.create({slideSize:{width:1280,height:720}});const s=p.slides.add();
const t=s.shapes.add({geometry:'textbox',position:{left:60,top:60,width:1100,height:130},fill:'none',line:{fill:'none',width:0}});t.text='3DIC 多目标全局布线算法';t.text.style={fontFamily:'Microsoft YaHei',fontSize:48,color:'#0b1730',insets:0};
await fs.writeFile(new URL('./smoke.png',import.meta.url),new Uint8Array(await(await p.export({slide:s,format:'png',scale:1})).arrayBuffer()));
console.log('smoke OK');
