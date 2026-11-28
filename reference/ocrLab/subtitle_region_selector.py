"""Windows subtitle ROI selector, compatible with OCR V25."""
import argparse
import json
import sys
import traceback
from pathlib import Path
from datetime import datetime


def select_video():
    from tkinter import filedialog
    return filedialog.askopenfilename(title='Chon video', filetypes=[('Video', '*.mp4 *.mkv *.mov *.avi *.webm *.m4v'), ('All files', '*.*')])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('video', nargs='?')
    parser.add_argument('--output')
    args = parser.parse_args()
    import tkinter as tk
    from tkinter import messagebox, ttk
    root = tk.Tk()
    root.withdraw()
    cap = None
    try:
        import cv2
        from PIL import Image, ImageTk
        path = args.video or select_video()
        if not path:
            return 0
        path = Path(path).expanduser().resolve()
        out = Path(args.output).expanduser().resolve() if args.output else path.with_name(path.stem+'_subtitle_region.json')
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError(f'Khong mo duoc video: {path}')
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        import math
        if not math.isfinite(fps) or fps <= 0: fps = 25.0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, frame = cap.read()
        if not ok: raise RuntimeError('Khong doc duoc frame dau tien')
        h,w = frame.shape[:2]
        scale = min(1100/w, 700/h, max(1,root.winfo_screenwidth()-100)/w, max(1,root.winfo_screenheight()-200)/h, 1)
        pw,ph = max(1,round(w*scale)),max(1,round(h*scale))
        root.title('Subtitle Region Selector')
        root.resizable(False,False)
        status = tk.StringVar()
        ttk.Label(root,textvariable=status).pack(pady=5)
        canvas = tk.Canvas(root,width=pw,height=ph,highlightthickness=0,cursor='crosshair')
        canvas.pack()
        toolbar = ttk.Frame(root,padding=8); toolbar.pack(fill='x')
        state = {'frame':frame,'index':0,'region':None,'start':None,'photo':None}
        image_id = canvas.create_image(0,0,anchor='nw')
        def render():
            img = cv2.cvtColor(cv2.resize(state['frame'],(pw,ph),interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2RGB)
            state['photo'] = ImageTk.PhotoImage(Image.fromarray(img))
            canvas.itemconfigure(image_id,image=state['photo'])
            canvas.delete('roi')
            r=state['region']
            if r:
                x,y,rw,rh=r
                canvas.create_rectangle(x*pw/w,y*ph/h,(x+rw)*pw/w,(y+rh)*ph/h,outline='#00ff00',width=2,tags='roi')
            status.set(f'{w} x {h} | {state["index"]/fps:.2f}s | '+(f'ROI: {r}' if r else 'Keo chuot de chon vung phu de'))
        def seek(index):
            index=max(0,index)
            if count>0: index=min(index,count-1)
            cap.set(cv2.CAP_PROP_POS_FRAMES,index)
            ok,new=cap.read()
            if ok and new.shape[:2]==(h,w):
                state.update(frame=new,index=index); render()
            else: status.set('Khong doc duoc frame nay; giu frame truoc.')
        def point(e): return min(pw,max(0,e.x)),min(ph,max(0,e.y))
        def down(e): state['start']=point(e)
        def drag(e):
            if state['start'] is None:return
            canvas.delete('draft'); canvas.create_rectangle(*state['start'],*point(e),outline='yellow',width=2,tags='draft')
        def up(e):
            if state['start'] is None:return
            a,b=state['start'],point(e); state['start']=None
            canvas.delete('draft')
            x1,x2=sorted((round(a[0]*w/pw),round(b[0]*w/pw)))
            y1,y2=sorted((round(a[1]*h/ph),round(b[1]*h/ph)))
            if x2>x1 and y2>y1: state['region']=(x1,y1,x2-x1,y2-y1)
            render()
        def reset(): state['region']=None; state['start']=None; canvas.delete('draft'); render()
        def save():
            if not state['region']:
                messagebox.showwarning('ROI','Hay ve vung phu de truoc.',parent=root);return
            x,y,rw,rh=state['region']; pixels=dict(zip(('x','y','width','height'),(x,y,rw,rh)))
            norm=dict(zip(pixels,(x/w,y/h,rw/w,rh/h)))
            result={'video':str(path),'video_resolution':{'width':w,'height':h},'selected_frame_time_seconds':round(state['index']/fps,3),'region_pixels':pixels,'region_normalized':{k:round(v,8) for k,v in norm.items()},'region_percent':{k:round(v*100,4) for k,v in norm.items()},'created_at':datetime.now().astimezone().isoformat(timespec='seconds')}
            try:
                out.parent.mkdir(parents=True,exist_ok=True)
                tmp=out.with_name(out.name+'.tmp'); tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(out)
            except Exception as exc:
                messagebox.showerror('Loi luu JSON',str(exc),parent=root);return
            try:
                annotated=state['frame'].copy(); cv2.rectangle(annotated,(x,y),(min(w-1,x+rw),min(h-1,y+rh)),(0,255,0),3)
                ok,encoded=cv2.imencode('.png',annotated)
                if not ok: raise RuntimeError('PNG encode failed')
                encoded.tofile(str(out.with_name(out.stem+'_preview.png')))
            except Exception as exc:
                messagebox.showwarning('Da luu JSON',f'Khong luu duoc PNG: {exc}',parent=root)
            print(f'[SAVED] JSON: {out}',flush=True)
            root.destroy()
        canvas.bind('<Button-1>',down); canvas.bind('<B1-Motion>',drag); canvas.bind('<ButtonRelease-1>',up)
        for label,delta in [('-5s',-5),('-1s',-1),('+1s',1),('+5s',5)]:
            ttk.Button(toolbar,text=label,command=lambda d=delta:seek(state['index']+round(d*fps))).pack(side='left',padx=2)
        ttk.Button(toolbar,text='Reset (R)',command=reset).pack(side='left',padx=5)
        ttk.Button(toolbar,text='Luu va dong (S)',command=save).pack(side='right',padx=3)
        ttk.Button(toolbar,text='Thoat (Q)',command=root.destroy).pack(side='right')
        for key,delta in [('a',-1),('d',1),('j',-5),('l',5)]:
            for k in (key,key.upper()): root.bind(k,lambda e,d=delta:seek(state['index']+round(d*fps)))
        for key in ('s','S','<Return>'): root.bind(key,lambda e:save())
        for key in ('r','R'): root.bind(key,lambda e:reset())
        for key in ('q','Q','<Escape>'): root.bind(key,lambda e:root.destroy())
        render(); seek(round(10*fps)); root.deiconify(); root.lift(); root.focus_force(); root.mainloop()
        return 0
    except Exception as exc:
        traceback.print_exc()
        messagebox.showerror('Subtitle Region Selector',f'{exc}\n\nPython: {sys.executable}',parent=root)
        root.destroy(); return 1
    finally:
        if cap is not None: cap.release()

if __name__=='__main__':
    sys.exit(main())
