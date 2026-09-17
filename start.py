import os, threading
import sitecustomize  # noqa: F401 - install market-data fallback first
import app_auto

# Recalculate budget/risk quantities immediately when inputs change.
_extra_js = r'''<script>
(function(){
  function bind(){
    ['budget','risk','maxtrades'].forEach(function(id){
      var el=document.getElementById(id);
      if(!el || el.dataset.reactiveBound) return;
      el.dataset.reactiveBound='1';
      el.addEventListener('input', function(){ if(typeof state==='function') state(); });
      el.addEventListener('change', function(){ if(typeof state==='function') state(); });
    });
  }
  setTimeout(bind,100);
})();
</script>'''
app_auto.HTML = app_auto.HTML.replace('</body></html>', _extra_js + '</body></html>')

if __name__ == '__main__':
    threading.Thread(target=app_auto.auto_loop, daemon=True).start()
    port=int(os.environ.get('PORT','10000'))
    app_auto.ThreadingHTTPServer(('0.0.0.0',port), app_auto.Handler).serve_forever()
