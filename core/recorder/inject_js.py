# -*- coding: utf-8 -*-
"""
注入到网页的录制监听脚本（JS）。
通过 QWebChannel 与 Python 双向通信：
  - Python 注入 build_inject_js() 到页面；
  - 页面事件经 window.__RPA_BRIDGE.recordAction(json) 回传 Python。
"""


def build_inject_js(active: bool, record_scroll: bool, record_hover: bool) -> str:
    """
    生成注入脚本。
    :param active: 是否立即开始记录（True=录制中）
    :param record_scroll: 是否记录滚动
    :param record_hover: 是否记录悬停
    """
    active_s = "true" if active else "false"
    scroll_s = "true" if record_scroll else "false"
    hover_s = "true" if record_hover else "false"
    return r"""
(function(){
  var ACTIVE = """ + active_s + r""";
  var RECORD_SCROLL = """ + scroll_s + r""";
  var RECORD_HOVER = """ + hover_s + r""";
  window.__RPA_RECORDER_ACTIVE = ACTIVE;
  if(window.__RPA_RECORDER_INSTALLED){ bindEvents(); return; }
  window.__RPA_RECORDER_INSTALLED = true;

  function loadChannel(cb){
    if(window.__RPA_BRIDGE){ cb(); return; }
    if(typeof qt === 'undefined' || !qt.webChannelTransport){
      // 无 channel 环境（极少见），延时重试
      setTimeout(function(){ loadChannel(cb); }, 300);
      return;
    }
    var s = document.createElement('script');
    s.src = 'qrc:///qtwebchannel/qwebchannel.js';
    s.onload = function(){
      new QWebChannel(qt.webChannelTransport, function(ch){
        window.__RPA_BRIDGE = ch.objects.rpaBridge;
        cb();
      });
    };
    s.onerror = function(){ setTimeout(function(){ loadChannel(cb); }, 300); };
    (document.head || document.documentElement).appendChild(s);
  }

  function send(action){
    if(!window.__RPA_RECORDER_ACTIVE || !window.__RPA_BRIDGE){ return; }
    action.url = location.href;
    try { action.timestamp = new Date().toISOString(); } catch(e){}
    try { window.__RPA_BRIDGE.recordAction(JSON.stringify(action)); } catch(e){}
  }

  function cssPath(el){
    if(!el || el.nodeType!==1) return null;
    var parts = [];
    while(el && el.nodeType===1 && el !== document.documentElement){
      var part = el.tagName.toLowerCase();
      if(el.id){ parts.unshift('#'+CSS.escape(el.id)); return parts.join(' > '); }
      var sib = el, nth = 1;
      while((sib = sib.previousElementSibling)){
        if(sib.tagName === el.tagName) nth++;
      }
      part += ':nth-of-type('+nth+')';
      parts.unshift(part);
      el = el.parentElement;
      if(parts.length > 25) break;
    }
    return (parts.length ? parts.join(' > ') : null);
  }

  // 判断某个 CSS 选择器在页面上是否唯一匹配
  function isUnique(css){
    try { return document.querySelectorAll(css).length === 1; } catch(e){ return false; }
  }

  function genSelector(el){
    if(!el || el.nodeType!==1) el = (el && el.parentElement) || document.body;
    var s = {css:null,xpath:null,id:null,role:null,name:null,text:null,tag:(el.tagName||'').toLowerCase()};

    // 1) 最优先：唯一 id
    if(el.id && isUnique('#'+CSS.escape(el.id))){
      s.id = '#'+CSS.escape(el.id);
    }

    // 2) data-* 唯一属性
    var dt = el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy');
    if(dt && isUnique('[data-testid="'+CSS.escape(dt)+'"]')){
      s.css = '[data-testid="'+CSS.escape(dt)+'"]';
      return s;
    }

    // 3) aria-label / alt / title 作为辅助
    var role = el.getAttribute('role') || implicitRole(el);
    if(role) s.role = role;
    var name = el.getAttribute('aria-label') || el.getAttribute('alt') || el.getAttribute('title');
    if(name) s.name = name.trim();

    // 4) 短文本（按钮/链接上的纯文本）
    var txt = (el.innerText || el.value || '').trim();
    if(txt && txt.length <= 40) s.text = txt;

    // 5) 生成 CSS 路径并验证唯一性；若不唯一则逐层加深直到唯一
    if(!s.css){
      s.css = bestUniqueCss(el);
    }
    return s;
  }

  // 从元素往上构建越来越长的 CSS 路径，直到在页面上唯一
  function bestUniqueCss(el){
    var cur = el;
    var chain = [];
    var attempts = 0;
    while(cur && cur.nodeType===1 && cur !== document.documentElement && attempts < 25){
      attempts++;
      var part = cur.tagName.toLowerCase();
      if(cur.id){
        chain.unshift('#'+CSS.escape(cur.id));
        var p = chain.join(' > ');
        if(isUnique(p)) return p;
        break; // 已到 id 层仍不唯一，不再向上
      }
      // 组合 class 以增加区分度
      var cls = cur.className;
      if(cls && typeof cls === 'string'){
        var cs = cls.trim().split(/\s+/).filter(function(c){ return c && c.indexOf('__')<0 && c.length<30; }).slice(0,2);
        if(cs.length){ part += '.' + cs.map(function(c){ return CSS.escape(c); }).join('.'); }
      }
      var sib = cur, nth = 1;
      while((sib = sib.previousElementSibling)){ if(sib.tagName === cur.tagName) nth++; }
      part += ':nth-of-type('+nth+')';
      chain.unshift(part);
      var cand = chain.join(' > ');
      if(isUnique(cand)) return cand;
      cur = cur.parentElement;
    }
    // 最后兜底：返回最长的一条路径（至少比单纯的 nth-of-type 强）
    return chain.length ? chain.join(' > ') : cssPath(el);
  }

  // 极简的隐式 role推断（便于生成 getByRole）
  function implicitRole(el){
    var t = (el.tagName||'').toLowerCase();
    if(t==='button') return 'button';
    if(t==='a') return 'link';
    if(t==='input'){
      var it=(el.getAttribute('type')||'text').toLowerCase();
      if(it==='button'||it==='submit'||it==='reset') return 'button';
      if(it==='checkbox') return 'checkbox';
      if(it==='radio') return 'radio';
    }
    if(t==='select') return 'combobox';
    return el.getAttribute('role');
  }

  function isEditable(el){
    var t=(el.tagName||'').toLowerCase();
    return t==='input'||t==='textarea'||t==='select'||el.isContentEditable;
  }

  // 输入防抖：连续输入只记录最终值
  var fillTimers = {};
  function debounceFill(el){
    var key = (el.id||el.name||cssPath(el)||Math.random());
    clearTimeout(fillTimers[key]);
    fillTimers[key] = setTimeout(function(){
      send({type:'fill', selector:genSelector(el), value: el.value});
    }, 600);
  }

  function bindEvents(){
    var doc = document;
    if(doc.__RPA_BOUND) return;
    doc.__RPA_BOUND = true;

    doc.addEventListener('click', function(e){
      var el = e.target.closest ? e.target.closest('a,button,input,select,textarea,[role=button],[onclick],label') : e.target;
      if(!el) el = e.target;
      if(!isEditable(el) || el.tagName==='SELECT'){
        send({type:'click', selector:genSelector(el)});
      }
    }, true);

    doc.addEventListener('change', function(e){
      var el = e.target, tag=(el.tagName||'').toLowerCase();
      if(tag==='select'){
        send({type:'select_option', selector:genSelector(el), value: el.value});
      } else if(tag==='input'){
        var t=(el.getAttribute('type')||'text').toLowerCase();
        if(t==='checkbox'||t==='radio'){
          send({type:'check', selector:genSelector(el), value: el.checked ? 'checked' : 'unchecked'});
        } else {
          debounceFill(el);
        }
      } else if(tag==='textarea'){
        debounceFill(el);
      }
    }, true);

    doc.addEventListener('input', function(e){
      var el=e.target, tag=(el.tagName||'').toLowerCase();
      if((tag==='input'&&['text','search','email','tel','password','url','number'].indexOf((el.getAttribute('type')||'text').toLowerCase())>=0)||tag==='textarea'){
        debounceFill(el);
      }
    }, true);

    doc.addEventListener('keydown', function(e){
      if(e.key==='Enter'||e.key==='Tab'||e.key==='Escape'){
        send({type:'press', selector:{css:'body',tag:'body'}, value: e.key});
      }
    }, true);

    if(RECORD_SCROLL){
      var scrollTimer=null;
      window.addEventListener('scroll', function(){
        clearTimeout(scrollTimer);
        scrollTimer=setTimeout(function(){
          send({type:'scroll', value: JSON.stringify({x:window.scrollX,y:window.scrollY})});
        }, 500);
      }, true);
    }

    if(RECORD_HOVER){
      doc.addEventListener('mouseover', function(e){
        var el=e.target.closest ? e.target.closest('[onmouseover],[role=button]') : null;
        if(el){ send({type:'hover', selector:genSelector(el)}); }
      }, true);
    }

    send({type:'ready', value:'recorder bound'});
  }

  loadChannel(bindEvents);
})();
"""


def build_deactivate_js() -> str:
    """停止录制时注入：关闭事件上报（保留监听，下次激活无需重连 channel）。"""
    return "window.__RPA_RECORDER_ACTIVE = false;"


def build_activate_js() -> str:
    """已安装监听后，重新激活上报。"""
    return "window.__RPA_RECORDER_ACTIVE = true; if(window.__RPA_BIND&&!window.__RPA_BOUND2){window.__RPA_BOUND2=true;try{send({type:'ready',value:'re-activated'});}catch(e){}}"
