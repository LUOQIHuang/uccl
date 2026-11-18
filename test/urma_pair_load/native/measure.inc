inline bool valid_load_shape(size_t lanes,size_t limit,size_t count){
  return (count==4 && ((lanes==1 && (limit==1 || limit==4)) || (lanes==4 && limit==4))) ||
         (count==12 && lanes==3 && (limit==3 || limit==12));
}
template<class Submit,class Poll>
std::string load_measure(size_t sequence,size_t lanes,size_t limit,size_t count,Submit submit,Poll poll){
  require(!current && !frame && valid_load_shape(lanes,limit,count),"invalid load shape");
  std::vector<Pair> pairs(count);
  for(size_t i=0;i<count;++i){pairs[i].wid=(uint64_t(i%lanes)<<32)|(sequence*(count*2)+i*2);pairs[i].aid=pairs[i].wid+1;}
  struct Reset{~Reset(){frame=nullptr;}} reset;
  frame=&pairs;frame_polls=0;frame_high=0;frame_limit=limit;
  auto start=now();auto deadline=start+15000000000ULL;
  auto wait=[&](size_t first,size_t count){while(true){bool done=true;for(size_t i=first;i<first+count;++i)done&=pairs[i].done();if(done)break;poll();require(now()<deadline,"load completion timeout");}};
  for(size_t first=0;first<count;first+=limit){submit(first,limit,pairs);wait(first,limit);}
  auto end=now();require(frame_high==limit,"unexpected observed load concurrency");
  std::ostringstream s;s<<"{\"version\":1,\"clock\":\"CLOCK_MONOTONIC_RAW\",\"data_lanes\":"<<lanes
    <<",\"max_inflight_pairs\":"<<limit<<",\"observed_max_inflight_pairs\":"<<frame_high
    <<",\"pairs_expected\":"<<count<<",\"sdk_poll_calls\":"<<frame_polls<<",\"loop_api_ns\":"<<end-start<<",\"pairs\":[";
  for(size_t i=0;i<count;++i){auto const& p=pairs[i];require(p.wb && p.ab && p.we>=p.wb && p.ae>=p.ab && p.wc>=p.we && p.ac>=p.ae,"invalid load timestamps");
    if(i>=limit){for(size_t j=(i/limit-1)*limit;j<(i/limit)*limit;++j)
      require(p.wb>=std::max(pairs[j].wc,pairs[j].ac),"load wave overlap");}
    if(i)s<<',';
    s<<"{\"write_id\":"<<p.wid<<",\"atomic_id\":"<<p.aid<<",\"write_post_begin\":"<<p.wb<<",\"write_post_end\":"<<p.we
     <<",\"atomic_post_begin\":"<<p.ab<<",\"atomic_post_end\":"<<p.ae<<",\"write_cqe\":"<<p.wc<<",\"atomic_cqe\":"<<p.ac
     <<",\"write_poll_seq\":"<<p.ws<<",\"atomic_poll_seq\":"<<p.as<<",\"sdk_poll_calls\":"<<p.polls<<'}';
  }
  s<<"]}";return s.str();
}
