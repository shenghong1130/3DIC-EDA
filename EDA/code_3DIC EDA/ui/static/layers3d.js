"use strict";
// Offline orthographic 3D projection. Z is schematic, never a process thickness.
// Receives the existing UI's filtered data; owns no routing or replay state.
window.LayerView3D = class LayerView3D {
    constructor(canvas, {requestDraw, pickPin, pickObject, hover}) {
        this.canvas=canvas;this.requestDraw=requestDraw;this.pickPin=pickPin;this.pickObject=pickObject;this.hover=hover;
        this.camera={yaw:-.62,elevation:.48,zoom:1,panX:0,panY:0,center:[0,0,0]};
        this.spacing=1;this.labels=true;this.onlyNetPins=false;this.signature=null;this.scene=null;
        this.projectedPins=[];this.projectedLinks=[];this.projectedRects=[];this.projectedPlanes=[];this.drag=null;this.moved=false;
        canvas.addEventListener('contextmenu',e=>e.preventDefault());
        canvas.addEventListener('pointerdown',e=>{
            if(e.button!==0&&e.button!==1&&e.button!==2)return;
            this.drag={x:e.clientX,y:e.clientY,pan:e.shiftKey||e.button!==0};this.moved=false;
            canvas.setPointerCapture(e.pointerId);this.hideHover();e.preventDefault();
        });
        canvas.addEventListener('pointermove',e=>{
            if(this.drag){
                const dx=e.clientX-this.drag.x,dy=e.clientY-this.drag.y;
                if(Math.abs(dx)+Math.abs(dy)>1)this.moved=true;
                if(this.drag.pan){this.camera.panX+=dx;this.camera.panY+=dy;}
                else{this.camera.yaw+=dx*.008;this.camera.elevation=Math.max(-1.5,Math.min(1.5,this.camera.elevation+dy*.006));}
                this.drag.x=e.clientX;this.drag.y=e.clientY;this.requestDraw();
            }else this.inspect(e);
        });
        canvas.addEventListener('pointerup',e=>{
            const click=this.drag&&!this.moved&&!this.drag.pan;this.drag=null;
            if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);
            if(click){const hit=this.hit(e);if(hit?.pin)this.pickPin(hit.pin);else if(hit)this.pickObject?.({...hit,kind:hit.kind||'guide',g:hit.g||[...(hit.box||[]),hit.layers?.[0]]});}
        });
        canvas.addEventListener('pointercancel',()=>{this.drag=null;});
        canvas.addEventListener('pointerleave',()=>this.hideHover());
        canvas.addEventListener('wheel',e=>{e.preventDefault();this.camera.zoom=Math.max(.15,Math.min(80,this.camera.zoom*Math.exp(-e.deltaY*.001)));this.hideHover();this.requestDraw();},{passive:false});
    }
    reset(preset='default') {
        const angles={default:[-.62,.48],front:[0,0],side:[Math.PI/2,0],top:[0,Math.PI/2]};
        const [yaw,elevation]=angles[preset]||angles.default;
        this.camera={yaw,elevation,zoom:1,panX:0,panY:0,center:[0,0,0]};this.requestDraw();
    }
    clear() {
        this.scene=null;this.signature=null;this.projectedPins=[];this.projectedLinks=[];this.projectedRects=[];this.projectedPlanes=[];this.hideHover();
        this.canvas.getContext('2d').clearRect(0,0,this.canvas.width,this.canvas.height);
    }
    hideHover(){this.hover.hidden=true;}
    configure({spacing,labels,onlyNetPins}) {
        this.spacing=spacing;this.labels=labels;this.onlyNetPins=onlyNetPins;
    }
    isVisible(layer,s) {
        return s.layerNumbers.has(layer)&&(s.tier==='both'||(s.tier==='bottom'?layer<=10:layer>=11))&&(s.layer==='all'||+s.layer===layer);
    }
    viewportBox(d){
        const die=d.die,c=this.camera,r=this.canvas.getBoundingClientRect(),sin=Math.sin(c.elevation);
        if(!r.width||!r.height||Math.abs(sin)<.1)return die;
        const step=.085*this.spacing,heights=d.layers.map(l=>(l.number-1)*step+(l.number>=11?4*step:0)),low=Math.min(...heights),high=Math.max(...heights),middle=(low+high)/2;
        const scale=this.base(r.width,r.height)*c.zoom,unit=Math.max(die[2]-die[0],die[3]-die[1])/2,points=[];
        for(const z of [low-middle,high-middle])for(const x of [0,r.width])for(const y of [0,r.height]){
            const horizontal=(x-r.width/2-c.panX)/scale,vertical=(r.height/2+c.panY-y)/scale,depth=(Math.cos(c.elevation)*(z-c.center[2])-vertical)/sin;
            points.push([(Math.cos(c.yaw)*horizontal-Math.sin(c.yaw)*depth+c.center[0])*unit+(die[0]+die[2])/2,(Math.sin(c.yaw)*horizontal+Math.cos(c.yaw)*depth+c.center[1])*unit+(die[1]+die[3])/2]);
        }
        return [Math.max(die[0],Math.min(...points.map(p=>p[0]))),Math.max(die[1],Math.min(...points.map(p=>p[1]))),Math.min(die[2],Math.max(...points.map(p=>p[0]))),Math.min(die[3],Math.max(...points.map(p=>p[1])))];
    }
    makeScene(s) {
        const d=s.data,[x1,y1,x2,y2]=d.die,scale=2/Math.max(x2-x1,y2-y1),step=.085*this.spacing;
        const height=l=>(l-1)*step+(l>=11?step*4:0),numbers=d.layers.map(l=>l.number),middle=(height(Math.min(...numbers))+height(Math.max(...numbers)))/2;
        const point=(x,y,l)=>[(x-(x1+x2)/2)*scale,(y-(y1+y2)/2)*scale,height(l)-middle];
        s.layerNumbers=new Set(d.layers.map(l=>l.number));
        const visible=l=>this.isVisible(l,s),nodes=new Map();
        const node=id=>{
            if(nodes.has(id))return nodes.get(id);
            const n=s.node(id);if(!n)return null;
            const result={p:point(n.x,n.y,n.l),layer:n.l,id};nodes.set(id,result);return result;
        };
        const scene={planes:[],pins:[],pinRects:[],estimates:[],paths:[],vias:[],tsvs:[],caps:[],guides:[],ghost:[],focus:[],sourcePins:s.pins.length,invalidEdges:0,reference:s.reference};
        const plane=(l)=>({layer:l.number,name:'M'+l.number,color:l.number<=10?'#4776a7':'#bb8b51',corners:[point(x1,y1,l.number),point(x2,y1,l.number),point(x2,y2,l.number),point(x1,y2,l.number)],grid:[]});
        for(const l of d.layers)if(visible(l.number)){
            const plate=plane(l);
            if(s.grid){
                const xs=d.x_lines,ys=d.y_lines;
                for(let i=0;i<xs.length;i+=Math.max(1,Math.ceil(xs.length/10)))plate.grid.push([point(xs[i],y1,l.number),point(xs[i],y2,l.number)]);
                for(let i=0;i<ys.length;i+=Math.max(1,Math.ceil(ys.length/10)))plate.grid.push([point(x1,ys[i],l.number),point(x2,ys[i],l.number)]);
            }
            scene.planes.push(plate);
        }
        for(const pin of s.pins){
            if(this.onlyNetPins&&s.selected!=='all'&&pin.net!==s.selected)continue;
            const p=point(pin.p.x,pin.p.y,pin.p.layer),focused=pin.net===s.selected;
            if(focused)scene.focus.push(p);
            if(s.showPins)scene.pins.push({p,pin,focused,color:pin.cluster?'#8b99a9':s.color(pin.net),layer:pin.p.layer});
        }
        const addEdge=(edge,net,ghost=false)=>{
            const a=node(edge[0]),b=node(edge[1]);if(!a||!b)return;
            const av=visible(a.layer),bv=visible(b.layer);if(!av&&!bv)return;
            const item={a:a.p,b:b.p,layers:[a.layer,b.layer],edge,net,color:ghost?(s.ghost.remove?'#d95062':'#12a178'):s.color(net),ghost};
            if(a.layer===b.layer){
                if(!s.paths)return;
                item.kind='route';(ghost?scene.ghost:scene.paths).push(item);
            }else{
                const tsv=Math.min(a.layer,b.layer)===10&&Math.max(a.layer,b.layer)===11;
                // Never invent a connection from merely overlapping guides or nodes.
                if(a.p[0]!==b.p[0]||a.p[1]!==b.p[1]||Math.abs(a.layer-b.layer)!==1){scene.invalidEdges++;return;}
                if(tsv?!s.tsvs:!s.paths)return;
                item.kind=tsv?'tsv':'via';
                if(av&&bv)(ghost?scene.ghost:tsv?scene.tsvs:scene.vias).push(item);
                else scene.caps.push({...item,p:av?a.p:b.p,layer:av?a.layer:b.layer});
            }
            if(net===s.selected){if(av)scene.focus.push(a.p);if(bv)scene.focus.push(b.p);}
        };
        const showPaths=s.paths&&(s.selected!=='all'||s.overviewPaths)&&!s.reference;
        // Path/Guide overview follows the existing 150-Net geometry budget.
        for(const n of s.nets){
            for(const p of n.pins||[])if(p.box&&visible(p.layer)){
                const b=p.box,corners=[point(b[0],b[1],p.layer),point(b[2],b[1],p.layer),point(b[2],b[3],p.layer),point(b[0],b[3],p.layer)];
                if(n.net===s.selected)scene.focus.push(...corners);
                if(s.pinRects)scene.pinRects.push({corners,pin:{p,net:n.net},layer:p.layer});
            }
            if(s.estimatedPaths)for(const e of n.estimated_paths?.segments||[])if(visible(e.layer))scene.estimates.push({a:point(...e.a,e.layer),b:point(...e.b,e.layer),layers:[e.layer],net:n.net,kind:'estimate',color:'#078a88',g:n.guides[e.guide_index]});
            if(n.net===s.selected)for(const g of n.guides||[])if(visible(g[4]))scene.focus.push(point(g[0],g[1],g[4]),point(g[2],g[3],g[4]));
            if(showPaths)for(const edge of n.edges||[]){
                const a=node(edge[0]),b=node(edge[1]);
                if(a&&b&&!(Math.min(a.layer,b.layer)===10&&Math.max(a.layer,b.layer)===11))addEdge(edge,n.net);
            }
            if(s.guides)for(const g of n.guides||[])if(visible(g[4])){
                const corners=[point(g[0],g[1],g[4]),point(g[2],g[1],g[4]),point(g[2],g[3],g[4]),point(g[0],g[3],g[4])];
                scene.guides.push({corners,net:n.net,g,kind:'guide',layers:[g[4]],color:d.capabilities?.route_edges===false?'hsl('+((g[4]*47)%360)+',55%,43%)':s.color(n.net)});if(n.net===s.selected)scene.focus.push(...corners);
            }
        }
        // TSVs use actual accepted edges, independently of the path toggle.
        if(s.tsvs&&!s.reference)for(const n of s.active.values())if(s.allowed.has(n.net)&&(s.selected==='all'||n.net===s.selected)&&n.cross_die){
            for(const edge of n.edges||[]){const a=node(edge[0]),b=node(edge[1]);if(a&&b&&Math.min(a.layer,b.layer)===10&&Math.max(a.layer,b.layer)===11)addEdge(edge,n.net);}
            // Explicit Guide-selection metadata, never inserted into actual edges.
            if(d.capabilities?.guide_tsv&&!n.trace_tsv_edges?.length)for(const marker of n.tsv_markers||[]){
                const [la,lb]=marker.layers,av=visible(la),bv=visible(lb);if(!av&&!bv)continue;
                const a=point(marker.x,marker.y,la),b=point(marker.x,marker.y,lb);
                const item={a,b,layers:[la,lb],net:n.net,kind:'guide_tsv',box:marker.box};
                if(av&&bv)scene.tsvs.push(item);else scene.caps.push({...item,p:av?a:b,layer:av?la:lb});
                if(n.net===s.selected){if(av)scene.focus.push(a);if(bv)scene.focus.push(b);}
            }
            if(d.capabilities?.guide_tsv&&!n.trace_tsv_edges?.length&&!n.tsv_markers?.length)for(const box of n.tsv_candidates||[])for(const l of [10,11])if(visible(l)){
                scene.guides.push({corners:[point(box[0],box[1],l),point(box[2],box[1],l),point(box[2],box[3],l),point(box[0],box[3],l)],net:n.net,g:[...box,l],kind:'tsv_candidate',layers:[l],color:'#e18b17'});
            }
        }
        if(showPaths&&s.ghost&&(s.selected==='all'||s.ghost.net===s.selected)&&s.allowed.has(s.ghost.net))for(const edge of s.ghost.edges||[])addEdge(edge,s.ghost.net,true);
        if(s.replay){
            const e=s.replay,a=e.a||e.parent,b=e.b||e.node;
            if(b&&visible(b[2]))scene.estimates.push({a:point(...(a||b)),b:point(...b),layers:[b[2]],net:e.net,kind:'INTERMEDIATE_ONLY',color:'#b348bb'});
        }
        return scene;
    }
    view(p) {
        const c=this.camera,x=p[0]-c.center[0],y=p[1]-c.center[1],z=p[2]-c.center[2];
        const horizontal=Math.cos(c.yaw)*x+Math.sin(c.yaw)*y,depth=-Math.sin(c.yaw)*x+Math.cos(c.yaw)*y;
        return [horizontal,-Math.sin(c.elevation)*depth+Math.cos(c.elevation)*z,Math.cos(c.elevation)*depth+Math.sin(c.elevation)*z];
    }
    fitSelection() {
        if(!this.scene?.focus.length)return;
        const points=this.scene.focus,c=this.camera;
        const low=[Infinity,Infinity,Infinity],high=[-Infinity,-Infinity,-Infinity];
        for(const p of points)for(let i=0;i<3;i++){low[i]=Math.min(low[i],p[i]);high[i]=Math.max(high[i],p[i]);}
        c.center=low.map((v,i)=>(v+high[i])/2);c.panX=c.panY=0;
        let width=.08,height=.08;
        for(const p of points){const v=this.view(p);width=Math.max(width,2*Math.abs(v[0]));height=Math.max(height,2*Math.abs(v[1]));}
        const r=this.canvas.getBoundingClientRect(),base=this.base(r.width,r.height);
        c.zoom=Math.max(.15,Math.min(80,Math.min((r.width-150)/width,(r.height-100)/height)/base));
    }
    // Fit the default oblique stack, including the enlarged Die gap.
    base(w,h){return Math.max(10,Math.min((w-140)/2.9,(h-80)/(1.29+.085*this.spacing*23*Math.cos(.48))));}
    render(s,fit=false) {
        this.state=s;
        this.selection=s.selected;
        const signature=[s.data,s.pins,s.active,s.allowed,s.index,s.selected,s.tier,s.layer,s.paths,s.overviewPaths,s.tsvs,s.guides,s.grid,s.reference,s.ghost,s.replay,s.showPins,s.estimatedPaths,s.pinRects,this.spacing,this.onlyNetPins];
        if(!this.signature||signature.some((v,i)=>v!==this.signature[i])){this.scene=this.makeScene(s);this.signature=signature;}
        if(fit)this.fitSelection();
        const canvas=this.canvas,r=canvas.getBoundingClientRect(),w=r.width,h=r.height;
        if(!w||!h)return;
        const ratio=Math.min(2,devicePixelRatio||1);
        if(canvas.width!==Math.round(w*ratio)||canvas.height!==Math.round(h*ratio)){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);}
        const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,w,h);
        ctx.fillStyle='#fcfdff';ctx.fillRect(0,0,w,h);
        const scale=this.base(w,h)*this.camera.zoom,scene=this.scene;
        const project=p=>{const v=this.view(p);return [w/2+this.camera.panX+v[0]*scale,h/2+this.camera.panY-v[1]*scale,v[2]];};
        const polygon=(corners,fill,stroke,alpha)=>{
            ctx.globalAlpha=alpha;ctx.beginPath();corners.forEach((p,i)=>{const q=project(p);i?ctx.lineTo(q[0],q[1]):ctx.moveTo(q[0],q[1]);});ctx.closePath();
            if(fill){ctx.fillStyle=fill;ctx.fill();}if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=.8;ctx.stroke();}ctx.globalAlpha=1;
        };
        const line=(a,b,color,width=1,alpha=1)=>{ctx.strokeStyle=color;ctx.lineWidth=width;ctx.globalAlpha=alpha;ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();ctx.globalAlpha=1;};
        const plates=[...scene.planes].sort((a,b)=>project(a.corners[0])[2]-project(b.corners[0])[2]);
        this.projectedPlanes=plates.map(p=>({...p,screen:p.corners.map(project)}));
        for(const plate of plates){
            polygon(plate.corners,plate.color,null,.024);polygon(plate.corners,null,plate.color,.38);
            for(const [a,b] of plate.grid)line(project(a),project(b),plate.color,.5,.12);
        }
        this.projectedRects=[];
        for(const guide of scene.guides){polygon(guide.corners,guide.color,guide.color,.12);this.projectedRects.push({...guide,screen:guide.corners.map(project)});}
        for(const rect of scene.pinRects){polygon(rect.corners,'#aa40ba','#aa40ba',.6);this.projectedRects.push({...rect,screen:rect.corners.map(project)});}
        this.projectedLinks=[];
        const drawLinks=(items,color,width)=>{
            const ordered=items.map(item=>({...item,pa:project(item.a),pb:project(item.b)})).sort((a,b)=>(a.pa[2]+a.pb[2])-(b.pa[2]+b.pb[2]));
            for(const item of ordered){
                if(Math.max(item.pa[0],item.pb[0])<0||Math.min(item.pa[0],item.pb[0])>w||Math.max(item.pa[1],item.pb[1])<0||Math.min(item.pa[1],item.pb[1])>h)continue;
                line(item.pa,item.pb,color||item.color,width,.94);this.projectedLinks.push(item);
                if(item.kind==='tsv'||item.kind==='guide_tsv')for(const p of [item.pa,item.pb]){ctx.fillStyle=color||'#d58218';ctx.fillRect(p[0]-2.5,p[1]-2.5,5,5);}
            }
        };
        ctx.setLineDash([5,3]);drawLinks(scene.estimates,null,2);ctx.setLineDash([]);
        drawLinks(scene.paths,null,2);drawLinks(scene.vias,'#65788e',2.7);drawLinks(scene.tsvs,'#e18b17',4.5);drawLinks(scene.ghost,null,3);
        for(const cap of scene.caps){const p=project(cap.p);ctx.fillStyle=cap.ghost?cap.color:cap.kind.includes('tsv')?'#e18b17':'#65788e';ctx.fillRect(p[0]-2.5,p[1]-2.5,5,5);this.projectedLinks.push({...cap,pa:p,pb:p});}
        const groups=new Map(),focused=[];this.projectedPins=[];
        for(const item of scene.pins){
            const p=project(item.p);if(p[0]<0||p[0]>w||p[1]<0||p[1]>h)continue;
            const q={...item,screen:p};this.projectedPins.push(q);
            if(item.focused){focused.push(q);continue;}
            const key=item.layer+','+Math.floor(p[0]/7)+','+Math.floor(p[1]/7);
            let group=groups.get(key);if(!group){group={x:0,y:0,count:0,color:item.color,net:item.pin.net,mixed:false,singles:[]};groups.set(key,group);}
            const count=item.pin.count||1;group.x+=p[0]*count;group.y+=p[1]*count;group.count+=count;group.aggregate||=!!item.pin.cluster;group.mixed||=!!item.pin.cluster||group.net!==item.pin.net;if(group.singles.length<2)group.singles.push(q);
        }
        let clusters=0,points=0;
        const dot=(x,y,color,radius,alpha)=>{ctx.globalAlpha=alpha;ctx.fillStyle=color;ctx.strokeStyle='#ffffffd9';ctx.lineWidth=.6;ctx.beginPath();ctx.arc(x,y,radius,0,2*Math.PI);ctx.fill();ctx.stroke();ctx.globalAlpha=1;};
        for(const g of groups.values()){
            if(g.count<3&&!g.aggregate)for(const q of g.singles){dot(q.screen[0],q.screen[1],q.color,2.2,s.selected==='all'?.8:.2);points++;}
            else{dot(g.x/g.count,g.y/g.count,g.mixed?'#8b99a9':g.color,Math.min(5,2+Math.log10(g.count)),s.selected==='all'?.65:.18);clusters++;}
        }
        for(const q of focused){dot(q.screen[0],q.screen[1],q.color,4.1,1);points++;}
        if(this.labels){
            ctx.font='11px "Segoe UI","Microsoft YaHei",sans-serif';const placed=[];
            for(const plate of [...plates].reverse()){
                const p=project(plate.corners[3]);if(p[1]<14||p[1]>h-8||p[0]<-60||p[0]>w-35)continue;
                if(placed.some(q=>Math.abs(q[0]-p[0])<70&&Math.abs(q[1]-p[1])<11))continue;
                placed.push(p);ctx.fillStyle='#ffffffdd';ctx.fillRect(p[0]-4,p[1]-10,68,12);ctx.fillStyle=plate.color;ctx.fillText(plate.name+(plate.layer===1?' · Die 1':plate.layer===11?' · Die 2':''),p[0],p[1]);
            }
        }
        this.stats={layers:scene.planes.length,pins:scene.pins.reduce((n,p)=>n+(p.pin.count||1),0),inView:this.projectedPins.reduce((n,p)=>n+(p.pin.count||1),0),points,clusters,paths:scene.paths.length,vias:scene.vias.length,tsvs:scene.tsvs.length,caps:scene.caps.length,guides:scene.guides.filter(g=>g.kind==='guide').length,invalidEdges:scene.invalidEdges};
        return this.stats;
    }
    hit(event) {
        const r=this.canvas.getBoundingClientRect(),x=event.clientX-r.left,y=event.clientY-r.top;
        let best=null,distance=10;
        for(const q of this.projectedPins){const d=Math.hypot(q.screen[0]-x,q.screen[1]-y);if(d<distance){distance=d;best={pin:q.pin,layer:q.layer};}}
        if(best)return best;
        for(let i=this.projectedLinks.length-1;i>=0;i--){
            const item=this.projectedLinks[i],a=item.pa,b=item.pb,dx=b[0]-a[0],dy=b[1]-a[1],t=Math.max(0,Math.min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy||1)));
            if(Math.hypot(x-a[0]-t*dx,y-a[1]-t*dy)<5)return item;
        }
        for(const item of [...(this.projectedRects||[])].reverse()){
            let inside=false;const ps=item.screen;
            for(let i=0,j=ps.length-1;i<ps.length;j=i++)if((ps[i][1]>y)!==(ps[j][1]>y)&&x<(ps[j][0]-ps[i][0])*(y-ps[i][1])/(ps[j][1]-ps[i][1])+ps[i][0])inside=!inside;
            if(inside)return item;
        }
        if(this.state?.cellAt)for(const plane of [...this.projectedPlanes].reverse()){
            const [a,b,,d]=plane.screen,ux=b[0]-a[0],uy=b[1]-a[1],vx=d[0]-a[0],vy=d[1]-a[1],det=ux*vy-uy*vx;
            if(Math.abs(det)<1e-6)continue;
            const u=((x-a[0])*vy-(y-a[1])*vx)/det,v=(ux*(y-a[1])-uy*(x-a[0]))/det;
            if(u<0||u>1||v<0||v>1)continue;
            const die=this.state.data.die,cell=this.state.cellAt(die[0]+u*(die[2]-die[0]),die[1]+v*(die[3]-die[1]),plane.layer<=10?'bottom':'top');
            if(cell){cell.hitLayer=plane.layer;return cell;}
        }
        return null;
    }
    inspect(event) {
        const hit=this.hit(event);this.hover.hidden=!hit;if(!hit)return;
        if(hit.kind==='cell'){this.hover.textContent='GCell ('+hit.gx+', '+hit.gy+') · M'+hit.hitLayer+' · 边界 ['+hit.box.join(', ')+'] · 点击查询 Guide 覆盖 / 资源来源';return;}
        if(hit.pin?.cluster){this.hover.textContent='聚合 '+hit.pin.count+' 个 Pin · M'+hit.layer+' · 点击后分页读取真实端子';return;}
        this.hover.textContent=hit.pin?'Pin '+hit.pin.p.name+' · '+hit.pin.net+' · M'+hit.layer+' · 几何中心 ('+hit.pin.p.x+', '+hit.pin.p.y+')'+(hit.pin.p.box?' · RECT ['+hit.pin.p.box.join(', ')+']':''):(hit.kind==='estimate'?'推导路径 / 非 Router 真实 A*':hit.kind==='guide'?'原始 Guide':hit.kind==='guide_tsv'?'Guide 解析 TSV（中心示意，非合法性结论）':hit.kind==='tsv_candidate'?'TSV 候选 / 未验证':hit.kind==='tsv'?'TSV 跨 Die':hit.kind==='via'?'普通 Via':'真实路径')+' · '+hit.net+' · '+hit.layers.map(l=>'M'+l).join(' ↔ ');
    }
};
