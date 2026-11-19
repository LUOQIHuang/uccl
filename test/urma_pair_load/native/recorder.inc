inline thread_local Pair* current=nullptr;
inline thread_local std::vector<Pair>* frame=nullptr;
inline thread_local std::vector<uint64_t>* frame_post_order=nullptr;
inline thread_local uint64_t frame_polls=0;
inline thread_local size_t frame_limit=0,frame_high=0;
inline Pair& find(uint64_t id){
  if(frame){for(auto& p:*frame)if(id==p.wid || id==p.aid)return p;}
  else if(current && (id==current->wid || id==current->aid))return *current;
  throw std::runtime_error("unexpected load request/completion");
}
inline uint64_t post_tick(){return (current || frame)?now():0;}
inline void posted(uint64_t id,bool write,bool atomic,uint64_t t){
  if((!current && !frame) || !t)return;
  auto& p=find(id);
  if(write && id==p.wid){require(!p.wb,"duplicate WRITE submission");p.wb=t;}
  else if(atomic && id==p.aid){require(!p.ab,"duplicate FADD submission");p.ab=t;}
  else throw std::runtime_error("invalid load opcode/identity");
  if(frame_post_order)frame_post_order->push_back(id);
  if(frame){size_t n=0;for(auto const& x:*frame)n+=x.wb && !x.done();frame_high=std::max(frame_high,n);require(n<=frame_limit,"load inflight limit exceeded");}
}
inline void post_end(uint64_t t,int rc){
  if((!current && !frame) || !t)return;
  require(rc==0,"load SDK post failed; retain DMA ownership");auto end=now();
  if(frame){for(auto& p:*frame){if(p.wb==t)p.we=end;if(p.ab==t)p.ae=end;}}
  else {if(current->wb==t)current->we=end;if(current->ab==t)current->ae=end;}
}
inline uint64_t poll_begin(){return frame?++frame_polls:(current?++current->polls:0);}
inline uint64_t poll_return(uint64_t seq){return seq?now():0;}
inline void completed(uint64_t id,uint64_t t,uint64_t seq,int status){
  if((!current && !frame) || !seq)return;
  require(status==0,"load completion error");auto& p=find(id);
  if(id==p.wid){require(!p.wc,"duplicate WRITE completion");p.wc=t;p.ws=seq;}
  else {require(!p.ac,"duplicate FADD completion");p.ac=t;p.as=seq;}
  if(frame)p.polls=frame_polls;
}
