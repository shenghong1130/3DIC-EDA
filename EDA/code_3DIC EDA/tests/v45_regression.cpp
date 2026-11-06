#include "router.hpp"
#include "allocation_guard.hpp"
#include <iostream>
using namespace gr;
namespace gr {struct RouterTestAccess{
 static void prepare(Router&r,size_t i,Route&v){v.guides.clear();r.make_guides(v);r.prepare_v34_route(r.d.nets[i],v);r.validate(r.d.nets[i],v);}
 static void optimize(Router&r){r.optimize_v45();}
 static void plan(Router&r){r.plan_v45();}
 static Route initial(Router&r,size_t i){return r.initial_v45(i);}
 static Route preview(Router&r,size_t i,const Route&o,Id site,Id seed){return r.preview_v45(i,o,site,seed);}
 static void fault(Router&r,bool heap){r.v45_install_hook_=[heap](){if(heap){fail_test_heap_allocations=true;throw std::bad_alloc();}throw std::runtime_error("Injected V45 failure");};}
 static void clear(Router&r){r.v45_install_hook_={};}
 static void pair_candidates(Router&r,const std::vector<Route>&next){r.v45_candidate_hook_=[next](size_t i,const Route&old,Id,Id seed){return seed?old:next.at(i);};}
 static void pair_fault(Router&r,const std::set<Id>&old,int after,bool heap){r.v45_install_hook_=[&r,old,after,heap,count=0]()mutable{
  bool changed=false;for(auto&t:r.routes)if(!t.nodes.empty()&&t.nodes!=old)changed=true;
  if(changed&&++count==after){if(heap){fail_test_heap_allocations=true;throw std::bad_alloc();}throw std::runtime_error("Injected pair failure");}};}
 static bool bounded(Router&r,const Route&old,Id site){try{auto c=r.preview_v45(0,old,site,0);r.validate(r.d.nets[0],c);return true;}catch(const Router::V3Budget&){return false;}catch(const Router::RerouteBudget&){return false;}}
 static Route forced(Router&r,const Net&n,Id v){r.v3_forced_tsv_=v;auto t=r.route(n);r.v3_forced_tsv_=0;return t;}
};}
void need(bool b,const char*m){if(!b)throw std::runtime_error(m);}
Design design(){Design d;d.dbu=1000;d.name="v43_cooperative_oracle";d.die={0,0,90000,90000,0};
 for(int k=0;k<=30;k++){d.grid['X'].push_back(k*3000);d.grid['Y'].push_back(k*3000);}
 for(int l=1;l<=20;l++){d.layers[l]={"metal"+std::to_string(l),"ROUTING",l%2?"HORIZONTAL":"VERTICAL",.1,0,3};d.tracks.push_back({l%2?'Y':'X',50,3000,30,l});if(l<20)d.vias.insert({l,l+1});}
 for(int i=0;i<2;i++){Net n;n.name="n"+std::to_string(i);for(int x:{2,22}){std::string name=n.name+"_"+std::to_string(x);double xx=x*3000+1550,yy=31550;d.io_pin_rects[name]={{xx-50,yy-50,xx+50,yy+50,1}};n.pins.push_back({name,xx,yy,1});}d.nets.push_back(n);}return d;}
Route corridor(Router&r,int kind){Route o;Id prior=0;
 auto point=[&](int x,int y,int l){Id v=r.id(x,y,l);o.nodes.insert(v);if(prior&&prior!=v)o.edges.insert({std::min(prior,v),std::max(prior,v)});prior=v;};
 if(kind==0){for(int x=2;x<=22;x++)point(x,10,1);}
 else{point(2,10,1);point(2,10,2);for(int y=11;y<=12;y++)point(2,y,2);point(2,12,1);
  if(kind==1){for(int x=3;x<=22;x++)point(x,12,1);}
  else{for(int x=3;x<=10;x++)point(x,12,1);point(10,12,2);point(10,13,2);point(10,14,2);point(10,14,1);for(int x=11;x<=14;x++)point(x,14,1);point(14,14,2);point(14,13,2);point(14,12,2);point(14,12,1);for(int x=15;x<=22;x++)point(x,12,1);}
  point(22,12,2);point(22,11,2);point(22,10,2);point(22,10,1);
 }return o;}
std::map<Id,int> usage(Router&r){std::map<Id,int>m;for(auto[v,s]:r.resources)if(s.usage)m[v]=s.usage;return m;}

int main(){try{
 Trace tr("",0);Config cfg;cfg.time_limit=120;cfg.iterations=0;
 auto d=design();d.nets.resize(1);Router r(d,cfg,tr);r.build_fixed_demand();
 auto old=corridor(r,2);for(auto&p:d.nets[0].pins)old.pin_access_nodes.push_back(r.pin_node(p));RouterTestAccess::prepare(r,0,old);r.routes[0]=old;r.occupy(old,1);
 Id hot=r.id(12,14,1);auto&s=r.resource(hot);s.fixed=s.scoring_fixed=1;s.capacity=s.scoring_capacity=1;s.scoring_ready=true;
 auto original=usage(r);r.v45.path=false;r.v45.thermal=false;r.v45.repair=true;r.v45.mode=0;r.v45.rounds=1;
 for(bool heap:{false,true}){RouterTestAccess::fault(r,heap);bool caught=false;
  try{RouterTestAccess::optimize(r);}catch(const std::bad_alloc&){fail_test_heap_allocations=false;caught=heap;}catch(const std::runtime_error&e){caught=std::string(e.what()).find("Injected V45")==0;}
  fail_test_heap_allocations=false;RouterTestAccess::clear(r);need(caught&&usage(r)==original&&r.routes[0].nodes==old.nodes,"V45 whole-round rollback failed");
 }
 r.cfg.time_limit=0;bool timeout=false;try{RouterTestAccess::optimize(r);}catch(const RouterTimeout&){timeout=true;}r.cfg.time_limit=120;need(timeout&&usage(r)==original,"Global timeout leaked usage");
 RouterTestAccess::optimize(r);need(r.overflow()==0,"Final repair failed real maze");r.validate(d.nets[0],r.routes[0]);r.occupy(r.routes[0],-1);need(usage(r).empty(),"Repair left duplicate usage");
 auto cross=design();cross.tsv_cut=true;for(auto&n:cross.nets){n.cross=true;n.pins[1].layer=11;cross.io_pin_rects[n.pins[1].name][0].layer=11;}
 Router t(cross,cfg,tr);t.build_fixed_demand();RouterTestAccess::plan(t);need(usage(t).empty(),"Planning changed real demand");
 for(size_t i=0;i<t.routes.size();i++){t.routes[i]=RouterTestAccess::initial(t,i);t.occupy(t.routes[i],1);t.validate(cross.nets[i],t.routes[i]);}
 auto prior=usage(t);auto saved=t.routes;for(bool heap:{false,true}){RouterTestAccess::fault(t,heap);try{RouterTestAccess::optimize(t);}catch(const std::bad_alloc&){fail_test_heap_allocations=false;}catch(const std::runtime_error&e){if(std::string(e.what()).find("Injected V45")!=0)throw;}fail_test_heap_allocations=false;RouterTestAccess::clear(t);}
 for(size_t i=0;i<t.routes.size();i++)t.validate(cross.nets[i],t.routes[i]);
 for(auto it=t.routes.rbegin();it!=t.routes.rend();++it)t.occupy(*it,-1);need(usage(t).empty(),"TSV full footprint release leaked");
 {
  auto pair=design();Router q(pair,cfg,tr);q.build_fixed_demand();q.v45.path=false;q.v45.thermal=false;q.v45.repair=true;q.v45.mode=0;q.v45.rounds=1;
  std::vector<Route>next;for(size_t i=0;i<2;i++){auto o=corridor(q,2);for(auto&p:pair.nets[i].pins)o.pin_access_nodes.push_back(q.pin_node(p));RouterTestAccess::prepare(q,i,o);q.routes[i]=o;q.occupy(o,1);auto c=corridor(q,int(i));c.pin_access_nodes=o.pin_access_nodes;RouterTestAccess::prepare(q,i,c);next.push_back(c);}
  auto&hot=q.resource(q.id(12,14,1));hot.fixed=hot.scoring_fixed=1;hot.capacity=hot.scoring_capacity=1;hot.scoring_ready=true;
  auto saved=usage(q);auto nodes=q.routes[0].nodes;RouterTestAccess::pair_candidates(q,next);
  for(int after:{1,2})for(bool heap:{false,true}){RouterTestAccess::pair_fault(q,nodes,after,heap);bool hit=false;
   try{RouterTestAccess::optimize(q);}catch(const std::bad_alloc&){fail_test_heap_allocations=false;hit=heap;}catch(const std::runtime_error&e){hit=std::string(e.what())=="Injected pair failure";}
   fail_test_heap_allocations=false;RouterTestAccess::clear(q);need(hit&&usage(q)==saved&&q.routes[0].nodes==nodes&&q.routes[1].nodes==nodes,"V45 pair rollback lost usage/tree");
  }
  RouterTestAccess::optimize(q);need(q.overflow()==0,"V45 joint repair failed");for(size_t i=0;i<2;i++){q.validate(pair.nets[i],q.routes[i]);q.occupy(q.routes[i],-1);}need(usage(q).empty(),"Pair double charging");
 }
 {
  auto big=design();big.nets.resize(1);big.tsv_cut=true;big.nets[0].cross=true;big.nets[0].pins[1].layer=11;big.io_pin_rects[big.nets[0].pins[1].name][0].layer=11;
  for(int k=2;k<65;k++){auto p=big.nets[0].pins[k%2];auto rects=big.io_pin_rects[p.name];p.name="extra"+std::to_string(k);big.io_pin_rects[p.name]=rects;big.nets[0].pins.push_back(p);}
  for(size_t k=1;k+1<big.grid['X'].size();k++)big.grid['X'][k]+=10*k; // nonuniform geometry, unchanged Pin shapes
  Router q(big,cfg,tr);q.build_fixed_demand();auto old=RouterTestAccess::forced(q,big.nets[0],q.id(12,12,10));q.routes[0]=old;q.occupy(old,1);auto saved=usage(q);
  RouterTestAccess::bounded(q,old,q.id(13,12,10));need(usage(q)==saved&&q.routes[0].nodes==old.nodes,"High Pin preview mutated occupancy");q.validate(big.nets[0],old);q.occupy(old,-1);need(usage(q).empty(),"Nonuniform high Pin footprint release");
 }
 std::cout<<"PASS V45 real maze, complete guide validation, rollback on bad_alloc/interruption/global timeout, two-die preview, TSV full footprint accounting\n";return 0;
}catch(const std::exception&e){fail_test_heap_allocations=false;std::cerr<<e.what()<<'\n';return 1;}}
