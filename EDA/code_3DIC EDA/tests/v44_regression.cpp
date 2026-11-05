#include "router.hpp"
#include "allocation_guard.hpp"
#include <iostream>
using namespace gr;
namespace gr {struct RouterTestAccess{
 static double price44(Router&r,Id v,int n){return r.v44_price(v,n);}
 static bool trial44(Router&r,Id seed,double&w,Json&j){r.v44_active_=true;return r.v44_trial(0,seed,0,w,1e100,j);}
 static void fault44(Router&r,bool heap){r.v44_install_hook_=[heap](){if(heap){fail_test_heap_allocations=true;throw std::bad_alloc();}throw std::runtime_error("Injected V44 failure");};}
 static void clear44(Router&r){r.v44_install_hook_={};}
 static double quality(Router&r){return r.batch_quality_full().phi;}
 static double price(Router&r,Id v,int n){return r.scoring_aware_congestion_cost(v,n);}
 static void prepare(Router&r,size_t i,Route&v){v.guides.clear();r.make_guides(v);r.prepare_v34_route(r.d.nets[i],v);r.validate(r.d.nets[i],v);}
 static double cost(Router&r){double w=0;for(auto&t:r.routes)w+=r.guide_grid_cost(t);return w;}
 static bool group(Router&r,const std::vector<size_t>&g,double&w,Json&j){return r.v43_try_group(g,std::vector<Id>(g.size(),r.id(10,12,1)),w,1e100,j);}
 static void candidates(Router&r,const std::vector<Route>&p){r.v43_candidate_hook_=[p](size_t i,const Route&,Id){return p.at(i);};}
 static void fault(Router&r,bool heap,int after=1){r.v43_install_hook_=[heap,after,count=0]()mutable{if(++count<after)return;if(heap){fail_test_heap_allocations=true;throw std::bad_alloc();}throw std::runtime_error("Injected V43 install failure");};}
 static void clear(Router&r){r.v43_install_hook_={};}
 static double refwire(Router&r,const Route&t){return r.v43_reference_wire(t);}
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
int main(){try{Trace tr("",0);Config cfg;cfg.time_limit=120;cfg.iterations=0;auto d=design();Router r(d,cfg,tr);r.build_fixed_demand();
 Route a=corridor(r,0),b=corridor(r,2);for(size_t i=0;i<2;i++){auto&t=i?b:a;for(auto&p:d.nets[i].pins)t.pin_access_nodes.push_back(r.pin_node(p));RouterTestAccess::prepare(r,i,t);r.routes[i]=t;r.occupy(t,1);}
 Id c=r.id(12,14,1);auto&fixed=r.resource(c);fixed.fixed=fixed.scoring_fixed=1;fixed.capacity=fixed.scoring_capacity=1;fixed.scoring_ready=true;
 std::vector<Route>next{corridor(r,1),corridor(r,0)};for(size_t i=0;i<2;i++){next[i].pin_access_nodes=r.routes[i].pin_access_nodes;RouterTestAccess::prepare(r,i,next[i]);}
 double before=RouterTestAccess::quality(r);auto original=usage(r);double w=RouterTestAccess::cost(r);Json j;
 for(size_t i=0;i<2;i++){r.occupy(r.routes[i],-1);r.occupy(next[i],1);need(RouterTestAccess::quality(r)>=before-1e-9,"Single Net unexpectedly improves cooperative fixture");r.occupy(next[i],-1);r.occupy(r.routes[i],1);}
 RouterTestAccess::candidates(r,next);
 for(auto group:std::vector<std::vector<size_t>>{{0,1},{1,0}}){
  for(bool heap:{false,true})for(int after:{1,2}){RouterTestAccess::fault(r,heap,after);bool hit=false;try{RouterTestAccess::group(r,group,w,j);}catch(const std::bad_alloc&){fail_test_heap_allocations=false;hit=heap;}catch(const std::runtime_error&e){hit=std::string(e.what()).find("Injected V43")==0;}
   fail_test_heap_allocations=false;RouterTestAccess::clear(r);need(hit&&usage(r)==original,"Atomic group failure leaked resource usage");need(r.routes[0].nodes==a.nodes&&r.routes[1].nodes==b.nodes,"Atomic group rollback changed Route");
  }
 }
 r.cfg.time_limit=0;bool timeout=false;try{RouterTestAccess::group(r,{1,0},w,j);}catch(const RouterTimeout&){timeout=true;}r.cfg.time_limit=120;need(timeout&&usage(r)==original,"Timeout rollback failure");
 need(RouterTestAccess::group(r,{0,1},w,j),"Cooperative candidate not accepted");need(RouterTestAccess::quality(r)<before,"Joint move did not reduce normalized penalty");
 std::cout<<"Cooperative fixture potential "<<before<<" -> "<<RouterTestAccess::quality(r)<<"\n";
 for(size_t i=0;i<2;i++){r.validate(d.nets[i],r.routes[i]);need(r.routes[i].pin_access_nodes==next[i].pin_access_nodes,"Pin access changed");}
 auto price_node=r.id(25,25,1);auto&s=r.resource(price_node);s.scoring_ready=true;s.scoring_fixed=0;s.scoring_capacity=s.capacity=1;s.usage=1;
 double low=RouterTestAccess::price(r,price_node,1);s.scoring_capacity=s.capacity=10;s.usage=10;double high=RouterTestAccess::price(r,price_node,1);need(low>high&&high>=0,"Capacity normalization/nonnegative toll failed");s.scoring_capacity=s.capacity=0;need(std::isinf(RouterTestAccess::price(r,price_node,1)),"Zero capacity was normalized as positive capacity");need(RouterTestAccess::price(r,price_node,0)==0,"Zero demand has a resource toll");bool negative=false;try{RouterTestAccess::price(r,price_node,-1);}catch(const std::runtime_error&){negative=true;}need(negative,"Negative resource demand accepted");s.usage=0;
 need(std::isfinite(RouterTestAccess::refwire(r,r.routes[0])),"Reference wire guard missing");
 for(auto it=r.routes.rbegin();it!=r.routes.rend();++it)r.occupy(*it,-1);need(usage(r).empty(),"Reverse release left demand");
 // Cross-Die group rollback exercises the complete 5um M10/M11 footprint,
 // not just ordinary graph-node occupancy. TSV anchors must remain fixed.
 {
  auto cross=design();cross.tsv_cut=true;
  for(auto&net:cross.nets){net.cross=true;net.pins[1].layer=11;cross.io_pin_rects[net.pins[1].name][0].layer=11;}
  Router t(cross,cfg,tr);t.build_fixed_demand();
  for(size_t i=0;i<t.routes.size();i++){t.routes[i]=RouterTestAccess::forced(t,cross.nets[i],t.id(12,12,10));t.occupy(t.routes[i],1);}
  auto saved=t.routes;auto old=usage(t);double cost=RouterTestAccess::cost(t);RouterTestAccess::candidates(t,saved);
  for(auto order:std::vector<std::vector<size_t>>{{0,1},{1,0}})for(int after:{1,2}){RouterTestAccess::fault(t,true,after);bool hit=false;try{RouterTestAccess::group(t,order,cost,j);}catch(const std::bad_alloc&){fail_test_heap_allocations=false;hit=true;}
   fail_test_heap_allocations=false;RouterTestAccess::clear(t);need(hit&&usage(t)==old,"TSV batch failed heap-safe resource restore");for(size_t i=0;i<t.routes.size();i++){need(t.routes[i].tsv==saved[i].tsv,"TSV batch changed anchor");t.validate(cross.nets[i],t.routes[i]);}
  }
  for(auto it=t.routes.rbegin();it!=t.routes.rend();++it)t.occupy(*it,-1);need(usage(t).empty(),"TSV group release leaked footprint");
 }
 // Actual V44 maze patch and undo: replace an overlong congested detour.
 {
  auto fixture=design();fixture.nets.resize(1);Router t(fixture,cfg,tr);t.build_fixed_demand();
  auto old=corridor(t,2);for(auto&p:fixture.nets[0].pins)old.pin_access_nodes.push_back(t.pin_node(p));RouterTestAccess::prepare(t,0,old);t.routes[0]=old;t.occupy(old,1);
  Id hot=t.id(12,14,1);auto&s=t.resource(hot);s.fixed=s.scoring_fixed=1;s.capacity=s.scoring_capacity=1;s.scoring_ready=true;
  auto saved=usage(t);double w=RouterTestAccess::refwire(t,old);Json detail;
  for(bool heap:{false,true}){RouterTestAccess::fault44(t,heap);bool hit=false;try{RouterTestAccess::trial44(t,hot,w,detail);}catch(const std::bad_alloc&){fail_test_heap_allocations=false;hit=heap;}catch(const std::runtime_error&e){hit=std::string(e.what()).find("Injected V44")==0;}fail_test_heap_allocations=false;RouterTestAccess::clear44(t);need(hit&&usage(t)==saved&&t.routes[0].nodes==old.nodes,"V44 patch failure must undo allocation-free");}
  need(RouterTestAccess::trial44(t,hot,w,detail),"V44 real maze must repair hotspot");t.validate(fixture.nets[0],t.routes[0]);need(t.overflow()==0,"V44 patch did not eliminate overflow");
  auto&s2=t.resource(t.id(25,25,1));s2.scoring_ready=true;s2.capacity=s2.scoring_capacity=0;
  need(std::isinf(RouterTestAccess::price44(t,t.id(25,25,1),1)),"V44 zero capacity became usable");need(RouterTestAccess::price44(t,hot,0)==0,"V44 zero demand toll");
 }
 std::cout<<"V44 real maze/rollback and V43 preserved two-Net legal corridor exchange (both individual moves nonimproving), normalized capacities, zero capacity, Pin access, arbitrary group order, allocation-free rollback, interruption and timeout PASS\n";return 0;
}catch(const std::exception&e){fail_test_heap_allocations=false;std::cerr<<e.what()<<'\n';return 1;}}
