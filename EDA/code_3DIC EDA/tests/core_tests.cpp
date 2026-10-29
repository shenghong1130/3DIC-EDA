#include "router.hpp"
#include <iostream>
#include <stdexcept>
#include <queue>
using namespace gr;
void require(bool b,const char*m){if(!b)throw std::runtime_error(m);}
Json result(const Router&r){std::ostringstream o;r.result_json(o);return Json::parse(o.str());}
Design tiny(){Design d;d.name="synthetic";d.version="5.8";d.dbu=1000;d.die={100,200,5100,5200,0};d.grid['X']={100,1100,2100,3100,4100,5100};d.grid['Y']={200,1200,2200,3200,4200,5200};d.tsv_cut=true;
 for(int l=1;l<=20;l++){d.layers[l]={"metal"+std::to_string(l),"ROUTING",l%2?"HORIZONTAL":"VERTICAL",0.1,0.1,0.2};d.tracks.push_back({'X',100,200,26,l});d.tracks.push_back({'Y',200,200,26,l});if(l<20&&l!=10)d.vias.insert({l,l+1});}return d;}
#include "algorithm_tests.hpp"
#include "tree_aware_tests.hpp"
int main(){try{
 algorithm_tests();
 tree_aware_tests();
 auto j=Json::parse("{\"a\":[1,true,null,\"\\u4e2d\"]}");require(j.at("a").arr[3].string()=="中","JSON unicode");require(Json::parse(j.dump()).at("a").arr.size()==4,"JSON roundtrip");bool failed=false;try{Json::parse("[1,]");}catch(...){failed=true;}require(failed,"Invalid JSON accepted");
 auto check=[](const std::string&o,double ex,double ey){auto[x,y]=orient_point(2,3,10,20,o);require(x==ex&&y==ey,"Orientation transform");};check("N",2,3);check("S",8,17);check("W",17,2);check("E",3,8);check("FN",8,3);check("FS",2,17);check("FW",17,8);check("FE",3,2);
 Design d=tiny();Config c;c.iterations=1;Trace t("",1);Router r(d,c,t);require(r.locate(r.xs,5100)==4,"Boundary GCell");require(r.locate(r.xs,1100)==1,"Half-open GCell");
 Net n{"multi",{{"a",600,700,1},{"b",4600,4700,1},{"c",1600,3700,2}},false};auto route=r.route(n);r.validate(n,route);require(route.nodes.count(r.pin_node(n.pins[2])),"Multi-pin missing");bool via=false;for(auto&e:route.edges){int x,y,l,xx,yy,ll;r.decode(e.a,x,y,l);r.decode(e.b,xx,yy,ll);via|=l!=ll;}require(via,"Via missing");
 auto before=r.overflow();r.occupy(route,1);r.occupy(route,-1);require(r.overflow()==before,"Rip-up occupancy mismatch");
 Net cross{"cross",{{"a",600,700,1},{"b",4600,4700,12},{"c",2600,2700,13}},true};auto cr=r.route(cross);r.validate(cross,cr);require(cr.cross&&cr.tsv!=0,"TSV missing");int xv,yv,lv;r.decode(cr.tsv,xv,yv,lv);require(lv==10&&cr.nodes.count(r.id(xv,yv,11)),"TSV layer mapping");
 auto v=r.pin_node(n.pins[0]);auto&res=r.resource(v);res.capacity=1;res.usage=4;require(r.overflow()==3,"Overflow formula");
 Design broken=tiny();broken.vias.clear();Router br(broken,c,t);failed=false;try{br.route(n);}catch(...){failed=true;}require(failed,"Missing via silently accepted");
 Design thermal=tiny();thermal.nets.push_back({"fixed_tsv",{{"a",4600,4700,10},{"b",4600,4700,11}},true});Router tr(thermal,c,t);for(size_t i=0;i<tr.heat.size();i++)tr.heat[i]=25+double(i);tr.run();
 double actual=result(tr).at("metrics").at("thermal_correlation_local").number();require(std::abs(actual-12/std::sqrt(1300*.96))<1e-12,"Analytic Pearson mismatch");
 std::fill(tr.heat.begin(),tr.heat.end(),25);require(result(tr).at("metrics").at("thermal_correlation_local").type==Json::Null,"Zero variance Pearson should be unavailable");
 auto tmp=std::filesystem::temp_directory_path()/("gr_result_"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
 require(std::filesystem::create_directory(tmp),"Cannot reserve JSON test directory");auto file=tmp/"result.json",partial=tmp/"result.json.tmp";
 write_json(file,Json("previous complete result"));
 failed=false;try{write_json(file,[](std::ostream&o){o<<"{\"partial\":";throw std::runtime_error("injected interruption");});}catch(...){failed=true;}
 require(failed&&Json::parse(read_file(file)).string()=="previous complete result"&&read_file(partial)=="{\"partial\":","Interrupted JSON published or partial lost");
 failed=false;try{tr.write_result(file);}catch(...){failed=true;}require(failed,"Stale temporary JSON overwritten");std::filesystem::remove(partial);
 tr.write_result(file);require(!std::filesystem::exists(partial)&&Json::parse(read_file(file)).at("nets").arr.size()==1,"JSON publication failed");
 std::filesystem::remove(file);std::filesystem::create_directory(file);
 failed=false;try{tr.write_result(file);}catch(const std::exception&e){failed=std::string(e.what()).find("publish failed")!=std::string::npos;}
 require(failed&&std::filesystem::is_directory(file)&&std::filesystem::is_regular_file(partial),"Failed publish destroyed destination or temporary file");
 std::filesystem::remove_all(tmp);
 std::cout<<"PASS: JSON, eight orientations, grid boundaries, multipin, preferred direction, via, TSV, occupancy, overflow, unreachable diagnostics\n";return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL: "<<e.what()<<'\n';return 1;}}
