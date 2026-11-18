template<class Submit,class Poll>
std::string load_measure(size_t sequence,size_t lanes,size_t limit,Submit submit,Poll poll){
  require(!current && !frame && ((lanes==1 && (limit==1 || limit==4)) || (lanes==4 && limit==4)),"invalid load shape");
  std::vector<Pair> pairs(4);
  for(size_t i=0;i<4;++i){pairs[i].wid=(uint64_t(i%lanes)<<32)|(sequence*8+i*2);pairs[i].aid=pairs[i].wid+1;}
  struct Reset{~Reset(){frame=nullptr;}} reset;
  frame=&pairs;frame_polls=0;frame_high=0;frame_limit=limit;
  auto start=now();auto deadline=start+15000000000ULL;
  auto wait=[&](size_t first,size_t count){while(true){bool done=true;for(size_t i=first;i<first+count;++i)done&=pairs[i].done();if(done)break;poll();require(now()<deadline,"load completion timeout");}};
  if(limit==1){for(size_t i=0;i<4;++i){submit(i,1,pairs);wait(i,1);}}
  else {submit(0,4,pairs);wait(0,4);}
  auto end=now();require(frame_high==limit,"unexpected observed load concurrency");
  std::ostringstream s;s<<"{\"version\":1,\"clock\":\"CLOCK_MONOTONIC_RAW\",\"data_lanes\":"<<lanes
    <<",\"max_inflight_pairs\":"<<limit<<",\"observed_max_inflight_pairs\":"<<frame_high
    <<",\"pairs_expected\":4,\"sdk_poll_calls\":"<<frame_polls<<",\"loop_api_ns\":"<<end-start<<",\"pairs\":[";
  for(size_t i=0;i<4;++i){auto const& p=pairs[i];require(p.wb && p.ab && p.we>=p.wb && p.ae>=p.ab && p.wc>=p.we && p.ac>=p.ae,"invalid load timestamps");
    if(limit==1 && i)require(p.wb>=std::max(pairs[i-1].wc,pairs[i-1].ac),"serial load overlap");
    if(i)s<<',';
    s<<"{\"write_id\":"<<p.wid<<",\"atomic_id\":"<<p.aid<<",\"write_post_begin\":"<<p.wb<<",\"write_post_end\":"<<p.we
     <<",\"atomic_post_begin\":"<<p.ab<<",\"atomic_post_end\":"<<p.ae<<",\"write_cqe\":"<<p.wc<<",\"atomic_cqe\":"<<p.ac
     <<",\"write_poll_seq\":"<<p.ws<<",\"atomic_poll_seq\":"<<p.as<<",\"sdk_poll_calls\":"<<p.polls<<'}';
  }
  s<<"]}";return s.str();
}
