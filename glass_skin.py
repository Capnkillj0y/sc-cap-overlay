"""Textured Glass Cockpit controller. Artwork is static; every value/control is live."""
import ctypes
import json
import math
import os
import tkinter as tk
from PIL import Image, ImageDraw, ImageFilter, ImageTk

WHITE = '#e8f6ff'
CYAN = '#29e8f8'
DIM = '#9dd8f5'
FONT = 'Bahnschrift' if os.name == 'nt' else 'DejaVu Sans'


def rgb(value):
    return tuple(int(value[i:i+2], 16) for i in (1,3,5))


def glass_button(width, height, color, subtle=False):
    """Independent live-state glass artwork with a frosted bevel and soft glow."""
    scale = 2
    w, h = max(2,int(width*scale)), max(2,int(height*scale))
    image = Image.new('RGBA', (w,h))
    draw = ImageDraw.Draw(image)
    base = rgb(color)
    for y in range(h):
        t=y/max(1,h-1)
        # Lit top bevel, translucent center, reflected light at the lower edge.
        factor=(.87 if base[1]>base[0] else .57)+.16*(1-t)+.23*math.exp(-((t-.04)/.08)**2)+.16*math.exp(-((t-.94)/.07)**2)
        row=tuple(min(255,int(c*factor)) for c in base)
        draw.line((0,y,w,y),fill=row+(245,))
    mask=Image.new('L',(w,h))
    ImageDraw.Draw(mask).rounded_rectangle((2,2,w-3,h-3),radius=9*scale,fill=255)
    image.putalpha(mask)
    draw.rounded_rectangle((2,2,w-3,h-3),radius=9*scale,
                           outline=(36,153,202,255) if subtle else tuple(min(255,int(c*.6+100)) for c in base)+(255,),width=scale if subtle else 2*scale)
    draw.rounded_rectangle((5,5,w-6,h-6),radius=7*scale,
                           outline=(75,176,222,65) if subtle else tuple(min(255,int(c*.5+100)) for c in base)+(100,),width=scale)
    # Blur the padded layer so the light fades out, without a rectangular cutoff.
    result=Image.new('RGBA',(w+32*scale,h+32*scale))
    result.alpha_composite(image,(16*scale,16*scale))
    glow=result.filter(ImageFilter.GaussianBlur(6*scale))
    glow.putalpha(glow.getchannel('A').point(lambda a: int(a*.6)))
    result=Image.alpha_composite(glow,result)
    return result.resize((int(width+32),int(height+32)),Image.Resampling.LANCZOS)


class Label:
    def __init__(self, surface, x, y, text='', size=20, color=DIM, anchor='w', bold=False, width=None):
        self.surface, self.x, self.y = surface, x, y
        self.text, self.color, self.size = text, color, size
        self.anchor,self.bold,self.width=anchor,bold,width
        self.tag='item'+str(len(surface.items))
        surface.items.append(self)
    def config(self, **kw):
        self.text=kw.get('text',self.text)
        self.color=kw.get('fg',self.color)
        self.surface.redraw_item(self)
    configure=config
    def cget(self,key):
        return self.text if key=='text' else self.color
    def draw(self):
        s=self.surface
        s.canvas.create_text(*s.xy(self.x,self.y),text=self.text,fill=self.color,
            font=(FONT,-max(8,round(self.size*s.scale)), 'bold' if self.bold else 'normal'),
            anchor=self.anchor,tags=self.tag,width=round(self.width*s.scale) if self.width else 0)


class Button(Label):
    def __init__(self, surface, box, text, command, size=19, text_x=None, style=None):
        x1,y1,x2,y2=box
        super().__init__(surface,text_x or (x1+x2)/2,(y1+y2)/2,text,size,WHITE,'center',True)
        self.box,self.command=box,command
        self.disabled=False
        self.style=style
        self._hovering=False
        self._image=None
        self._image_key=None
        surface.buttons.append(self)
    def set_text(self,text):
        self.config(text=text)
    def set_colors(self,bg=None,fg=None):
        if bg:
            self.style='red' if bg.lower() in ('#ff6868','#ff676e') else 'cyan' if bg.lower() in ('#32e4f4','#31e5df') else None
        self.color=WHITE if self.style=='red' else '#032837' if self.style=='cyan' else DIM
        self.surface.redraw_item(self)
    def set_disabled(self,disabled):
        self.disabled=bool(disabled)
        self.surface.redraw_item(self)
    def contains(self,x,y):
        a,b,c,d=self.box
        return a<=x<=c and b<=y<=d
    def draw(self):
        s=self.surface
        x1,y1,x2,y2=self.box
        style=self.style if not self.disabled else None
        if style:
            color={'red':'#fa5264','glass':'#153f5c'}.get(style,'#03def4')
            key=(round((x2-x1)*s.scale),round((y2-y1)*s.scale),color)
            if self._image_key!=key:
                self._image=ImageTk.PhotoImage(glass_button(key[0],key[1],color,subtle=style=='glass'))
                self._image_key=key
            x,y=s.xy(x1,y1)
            s.canvas.create_image(x-16,y-16,image=self._image,anchor='nw',tags=self.tag)
        color='#627d90' if self.disabled else self.color
        if self._hovering and not self.disabled:
            # Highlight only the rim, allowing the frosted artwork to remain visible.
            s.canvas.create_rectangle(*s.xy(x1+3,y1+3),*s.xy(x2-3,y2-3),outline='#8aeaff',width=1,tags=self.tag)
        s.canvas.create_text(*s.xy(self.x,self.y),text=self.text,fill=color,
            font=(FONT,-max(8,round(self.size*s.scale)),'bold'),anchor=self.anchor,tags=self.tag)


class Toggle(Button):
    def __init__(self,surface,x,y,text,command):
        super().__init__(surface,(x,y-19,x+295,y+20),text,command,size=19,text_x=x+98)
        self.anchor='w'
        self.value=False
        self.x=x+98
        self.y=y
    def get(self): return self.value
    def set(self,value):
        self.value=bool(value)
        self.surface.redraw_item(self)
    def activate(self):
        self.value=not self.value
        self.command()
        self.surface.redraw_item(self)
    def draw(self):
        s=self.surface
        x,y=self.box[0],self.y
        # Render a beveled glass track and an illuminated spherical knob.
        w,h=76,40
        key=(max(1,round(w*s.scale)),max(1,round(h*s.scale)),self.value,self.disabled)
        if self._image_key!=key:
            q=3
            im=Image.new('RGBA',(w*q,h*q))
            d=ImageDraw.Draw(im)
            active=self.value
            # The entire selected track lights blue, not just the moving knob.
            fill=(13,112,185,255) if active else (8,35,53,240)
            rim=(64,211,255,255) if active else (62,142,185,255)
            if active and self.disabled:
                fill=(16,78,127,255)
                rim=(58,151,203,255)
            d.rounded_rectangle((1*q,3*q,70*q,37*q),radius=18*q,fill=fill,outline=rim,width=1*q)
            d.rounded_rectangle((2*q,4*q,69*q,36*q),radius=16*q,outline=(116,220,255,180) if active else (83,162,203,130),width=q)
            if active:
                d.line((17*q,7*q,53*q,7*q),fill=(111,207,255,145),width=q)
            cx=52 if self.value else 24
            for r in range(13,0,-1):
                t=1-r/13
                base=(40,219,244) if self.value else (78,143,185)
                if self.disabled: base=(57,92,118)
                col=tuple(min(255,int(v+35*t)) for v in base)
                d.ellipse(((cx-r)*q,(20-r)*q,(cx+r)*q,(20+r)*q),fill=col+(255,))
            d.arc(((cx-13)*q,7*q,(cx+13)*q,33*q),185,340,fill=(178,230,250,190),width=q)
            self._image=ImageTk.PhotoImage(im.resize((key[0],key[1]),Image.Resampling.LANCZOS))
            self._image_key=key
        s.canvas.create_image(*s.xy(x,y-20),image=self._image,anchor='nw',tags=self.tag)
        color='#83afc8' if self.disabled else CYAN if self.value else DIM
        s.canvas.create_text(*s.xy(self.x,self.y),text=self.text,anchor='w',fill=color,
                            font=(FONT,-max(8,round(19*s.scale))),tags=self.tag)


class StatusLabel(Label):
    """Keep changing learner messages in the dashboard's uppercase typography."""
    def draw(self):
        original=self.text
        message=str(original).upper().replace(' - ','\n').replace(' · ','\n')
        self.text='\n'.join('\u2009'.join(line) for line in message.split('\n'))
        try:
            super().draw()
        finally:
            self.text=original


class Gauge:
    def __init__(self,surface):
        self.surface=surface
        self.tag='gauge'
        self.text='--'
        self.fraction=0
        self.color=CYAN
        self._key=None
        self._image=None
        surface.items.append(self)
    def set(self,text,fraction,color):
        new=(str(text),round(max(0,min(1,fraction)),3),color)
        if new==(self.text,self.fraction,self.color): return
        self.text,self.fraction,self.color=new
        self.surface.redraw_item(self)
    def draw(self):
        s=self.surface
        key=(round(self.fraction,3),self.color,round(s.scale,3))
        if key!=self._key:
            size=440
            q=2
            im=Image.new('RGBA',(size*q,size*q))
            d=ImageDraw.Draw(im)
            color=rgb(self.color)
            # Progress sits exactly on the illuminated track in the supplied artwork.
            for i in range(4):
                start=-90+i*90+.35
                end=min(-90+(i+1)*90-.35,-90+360*self.fraction)
                if end<=start: continue
                for r in range(26):
                    outer=190-r
                    light=.78+.22*math.sin(r/26*math.pi)
                    c=tuple(min(255,int(v*light)) for v in color)
                    d.arc(((220-outer)*q,(220-outer)*q,(220+outer)*q,(220+outer)*q),start,end,fill=c+(255,),width=2*q)
                d.arc((30*q,30*q,410*q,410*q),start,end,fill=(138,249,255,210),width=q)
            if self.fraction>0:
                d.ellipse((213*q,12*q,227*q,26*q),fill=color+(255,))
                d.line((220*q,4*q,220*q,12*q),fill=color+(255,),width=2*q)
            glow=im.filter(ImageFilter.GaussianBlur(5*q))
            glow.putalpha(glow.getchannel('A').point(lambda a: int(a*.48)))
            im=Image.alpha_composite(glow,im)
            self._image=ImageTk.PhotoImage(im.resize((max(1,round(size*s.scale)),max(1,round(size*s.scale))),Image.Resampling.LANCZOS))
            self._key=key
        existing=s.canvas.find_withtag(self.tag)
        if len(existing)==3:
            image_id,number_id,caption_id=existing
            s.canvas.itemconfigure(image_id,image=self._image)
            s.canvas.itemconfigure(number_id,text=self.text,fill=self.color)
        else:
            s.canvas.create_image(*s.xy(383-220,469-220),image=self._image,anchor='nw',tags=self.tag)
            s.canvas.create_text(*s.xy(383,464),text=self.text,fill=self.color,
                                font=(FONT,-max(8,round(110*s.scale)),'bold'),tags=self.tag)
            s.canvas.create_text(*s.xy(383,535),text='R E A D I N G',fill=CYAN,
                                font=(FONT,-max(8,round(20*s.scale)),'bold'),tags=self.tag)



class Hotkeys:
    """Adapter for the app's existing registration/status label."""
    def __init__(self,surface,app,module):
        self.surface,self.app,self.module=surface,app,module
        self.tag='hotkeys'
        self.text=''
        self.values=[]
        surface.items.append(self)
    def config(self,**kw):
        self.text=kw.get('text',self.text)
        try:
            with open(self.module.CONFIG_PATH) as f: cfg=json.load(f)
        except (OSError,ValueError): cfg={}
        self.values=[self.app._hotkey_spec(key,cfg) or 'Unset' for key in (
            'hotkey_toggle_monitoring','hotkey_test_flare','hotkey_false_alarm','hotkey_ai_assist')]
        self.surface.redraw_item(self)
    configure=config
    def draw(self):
        s=self.surface
        for i,(key,label) in enumerate(zip(self.values,('Start / Stop','Test Flare','False Alarm','AI Assist'))):
            x=804 if i%2==0 else 1187
            y=784 if i<2 else 831
            size=min(16, max(10, int(176/max(1,len(key)))))
            s.canvas.create_text(*s.xy(x,y),text=key.title(),fill=CYAN,
                font=(FONT,-max(8,round(size*s.scale)),'bold'),tags=self.tag)
            s.canvas.create_text(*s.xy(x+87,y),text=label,fill=DIM,anchor='w',
                font=(FONT,-max(8,round(17*s.scale))),tags=self.tag)
        # Registration problems are visible without replacing the shortcut tiles.
        warning=''
        if 'inactive' in self.text: warning='Global hotkeys require Windows'
        elif 'disabled' in self.text: warning='Hotkeys disabled'
        elif 'invalid' in self.text or 'in use' in self.text or 'No valid' in self.text: warning='Hotkey conflict or invalid binding · EDIT to review'
        if warning:
            s.canvas.create_text(*s.xy(794,746),text=warning,anchor='w',fill='#e9bc75',
                                font=(FONT,-max(8,round(11*s.scale))),tags=self.tag)


class Surface:
    def __init__(self,app,module):
        self.app,self.module=app,module
        self.root=app.root
        self.items,self.buttons=[],[]
        self.scale=1
        self.offset=(0,0)
        self.ready=False
        self.hover=None
        self.pressed=None
        self.drag=None
        self.resize=None
        self.maximized=False
        self.normal_geometry=None
        self._layout_job=None
        self._paint_job=None
        self._region_key=None
        self.source=Image.open(module.bundled_resource('glass_cockpit_skin.png')).convert('RGB').crop((50,82,1486,972))
        self.canvas=tk.Canvas(self.root,bg='#05111c',highlightthickness=0,bd=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',self.schedule_layout)
        self.canvas.bind('<Motion>',self.motion)
        self.canvas.bind('<Leave>',self.leave)
        self.canvas.bind('<ButtonPress-1>',self.press)
        self.canvas.bind('<B1-Motion>',self.drag_motion)
        self.canvas.bind('<ButtonRelease-1>',self.release)
        self.canvas.bind('<Double-Button-1>',self.double_click)
        self.root.bind('<Alt-F4>',lambda e:app.on_close())
        self.root.overrideredirect(True)
        self.root.minsize(860,534)
        width=min(1280,self.root.winfo_screenwidth()-60)
        height=round(width*890/1436)
        if height>self.root.winfo_screenheight()-90:
            height=self.root.winfo_screenheight()-90
            width=round(height*1436/890)
        self.root.geometry(f'{width}x{height}+40+40')
        self.root.bind('<Map>',self.mapped,add='+')
    def mapped(self,event):
        if event.widget!=self.root: return
        self.root.overrideredirect(True)
        self._region_key=None
        self.schedule_layout()
        if os.name=='nt':
            try:
                # GetParent (used here before) can return an inner wrapper
                # window, an owner window, or nothing useful, depending on
                # exactly how Tk nests its windows on Windows -- not
                # reliably the real top-level window the taskbar cares
                # about. GetAncestor(hwnd, GA_ROOT), via this same file's
                # own window_handle() helper (already proven correct --
                # it's what the rounded-corner window region uses), is the
                # actual documented way to get that window. Applying the
                # WS_EX_APPWINDOW fix-up to the wrong handle would silently
                # do nothing, leaving the real window without a taskbar
                # entry even while fully open.
                from desktop_window import window_handle
                user32=ctypes.windll.user32
                hwnd=window_handle(self.root)
                style=user32.GetWindowLongW(ctypes.c_void_p(hwnd),-20)
                user32.SetWindowLongW(ctypes.c_void_p(hwnd),-20,(style|0x40000)&~0x80)
            except (OSError,AttributeError): pass
    def xy(self,x,y):
        return ((x-50)*self.scale+self.offset[0],(y-82)*self.scale+self.offset[1])
    def original(self,event):
        return ((event.x-self.offset[0])/self.scale+50,(event.y-self.offset[1])/self.scale+82)
    def schedule_layout(self,event=None):
        if self._layout_job: self.root.after_cancel(self._layout_job)
        self._layout_job=self.root.after(25,self.layout)
    def layout(self):
        if self._layout_job is not None:
            self.root.after_cancel(self._layout_job)
            self._layout_job=None
        if not self.ready: return
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height()
        # Windows may report Tk's initial 1x1 placeholder before the native
        # window is mapped. Keep the last valid scale and wait for Configure
        # or Map; never render a whole dashboard at this placeholder size.
        if w <= 1 or h <= 1:
            return
        self.scale=min(w/1436,h/890)
        size=(max(1,round(1436*self.scale)),max(1,round(890*self.scale)))
        self.offset=((w-size[0])/2,(h-size[1])/2)
        self.background=ImageTk.PhotoImage(self.source.resize(size,Image.Resampling.LANCZOS))
        # A live rounded rim matches the native window region; the source art
        # remains intact. Region bounds follow the artwork when letterboxed.
        radius=max(12,round(32*self.scale))
        rim=Image.new('RGBA',size)
        d=ImageDraw.Draw(rim)
        d.rounded_rectangle((1,1,size[0]-2,size[1]-2),radius=radius,
                            outline='#12364c',width=max(3,round(5*self.scale)))
        d.rounded_rectangle((1,1,size[0]-2,size[1]-2),radius=radius,
                            outline='#368cad',width=1)
        self.frame_rim=ImageTk.PhotoImage(rim)
        if os.name=='nt':
            from desktop_window import window_handle, set_rounded_region
            try:
                hwnd=window_handle(self.root)
                # Re-assert the taskbar-visibility fix on every layout pass,
                # using this SAME freshly-fetched handle, not just once in
                # mapped(). overrideredirect() can recreate the native
                # window; if that happens between mapped()'s own handle
                # lookup and this one, mapped()'s fix would land on a
                # handle that's no longer the current window -- which would
                # show a correct taskbar entry for a moment, then lose it
                # the instant layout() runs and starts operating on the
                # real (different) one instead.
                user32=ctypes.windll.user32
                style=user32.GetWindowLongW(ctypes.c_void_p(hwnd),-20)
                user32.SetWindowLongW(ctypes.c_void_p(hwnd),-20,(style|0x40000)&~0x80)
                left,top=map(round,self.offset)
                bounds=(left,top,left+size[0],top+size[1])
                key=(hwnd,bounds,radius)
                if key!=self._region_key and set_rounded_region(hwnd,bounds,radius):
                    self._region_key=key
            except (OSError,AttributeError) as exc:
                print('Rounded window unavailable:',exc)
        self.repaint()
    def repaint(self):
        # Repaint the cached skin and live items together. Tk's incremental
        # damage handling can erase overlapping translucent images on updates.
        if self._paint_job is not None:
            self.root.after_cancel(self._paint_job)
            self._paint_job=None
        if not self.ready or not hasattr(self,'background'): return
        self.canvas.delete('all')
        self.canvas.create_image(*self.offset,image=self.background,anchor='nw',tags='background')
        self.canvas.create_image(*self.offset,image=self.frame_rim,anchor='nw',tags='frame')
        for item in self.items: item.draw()
    def redraw_item(self,item):
        if not self.ready or not hasattr(self,'background'): return
        # Coalesce status, buttons and gauge changes in the same UI tick.
        if self._paint_job is None:
            self._paint_job=self.root.after_idle(self.repaint)
    def button_at(self,x,y):
        return next((b for b in self.buttons if b.contains(x,y)),None)
    def motion(self,event):
        x,y=self.original(event)
        new=self.button_at(x,y)
        if new is not self.hover:
            if self.hover:
                self.hover._hovering=False
                self.redraw_item(self.hover)
            self.hover=new
            if new:
                new._hovering=True
                self.redraw_item(new)
        self.canvas.configure(cursor='hand2' if new and not new.disabled else 'sizing' if x>1450 and y>936 else '')
    def leave(self,event):
        if self.hover:
            self.hover._hovering=False
            self.redraw_item(self.hover)
            self.hover=None
    def press(self,event):
        x,y=self.original(event)
        self.pressed=self.button_at(x,y)
        if self.pressed: return
        if x>1450 and y>936:
            self.resize=(event.x_root,event.y_root,self.root.winfo_width(),self.root.winfo_height())
        elif y<125:
            self.drag=(event.x_root-self.root.winfo_x(),event.y_root-self.root.winfo_y())
    def drag_motion(self,event):
        if self.resize:
            x,y,w,h=self.resize
            width=max(860,w+event.x_root-x)
            height=max(534,h+event.y_root-y)
            self.root.geometry(f'{width}x{height}')
        elif self.drag and not self.maximized:
            self.root.geometry(f'+{event.x_root-self.drag[0]}+{event.y_root-self.drag[1]}')
    def release(self,event):
        x,y=self.original(event)
        button=self.pressed
        self.pressed=None
        self.drag=self.resize=None
        if button and not button.disabled and button.contains(x,y):
            if isinstance(button,Toggle): button.activate()
            else: button.command()
    def double_click(self,event):
        x,y=self.original(event)
        if y<125 and x<1250: self.maximize()
    def minimize(self):
        from desktop_window import clear_region
        clear_region(self.root)
        self._region_key=None
        self.root.overrideredirect(False)
        # overrideredirect isn't a simple style flip -- Tk tears down and
        # recreates the native window underneath, and Windows needs a real
        # additional turn of its own message loop to finish registering
        # that new window as a normal, taskbar-eligible one. update_idletasks()
        # only flushes Tk's own internal redraw/geometry queue in the same
        # call stack -- it does NOT pump the OS-level window-manager
        # messages this specific recreation depends on, so it wasn't
        # enough. Deferring iconify() with after() instead of calling it
        # synchronously lets that message loop actually run first.
        self.root.after(10, self.root.iconify)
    def maximize(self):
        if self.maximized:
            self.root.geometry(self.normal_geometry)
            self.maximized=False
        else:
            self.normal_geometry=self.root.geometry()
            self.root.geometry(f'{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()-48}+0+0')
            self.maximized=True


def build(app,module):
    s=Surface(app,module)
    app.glass_surface=s
    track=module._tracked
    Label(s,123,160,'SC //',40,CYAN,bold=True)
    Label(s,270,160,track('CAPACITOR'),40,WHITE,bold=True)
    Label(s,124,195,track('TACTICAL OVERLAY CONTROL'),17,DIM)
    app.status_dot=Label(s,1194,165,'●',34,CYAN,'center')
    app.status_label=Label(s,1224,165,track('READY'),18,CYAN,bold=True)
    for x,text in ((1318,'—'),(1374,'□'),(1429,'×')):
        command=s.minimize if x==1318 else s.maximize if x==1374 else app.on_close
        Button(s,(x-23,83,x+23,119),'',command)
    app.ring=Gauge(s)
    Label(s,789,229,track('QUICK POSITION'),18,DIM)
    app.position_buttons={}
    for i,(key,text) in enumerate((('center-high','HIGH'),('center-low','LOW'),('center-left','LEFT'),('center','CENTER'),('center-right','RIGHT'))):
        x=(728,873,1016,1158,1301)[i]
        app.position_buttons[key]=Button(s,(x,268,x+134,319),track(text),lambda k=key:app.set_overlay_position(k),17)
    app.calibrate_btn=Button(s,(705,355,1047,438),track('CALIBRATE'),app.do_calibrate,23,text_x=913)
    app.toggle_btn=Button(s,(1064,355,1453,438),track('START MONITORING'),app.toggle_monitoring,21,style='cyan')
    Label(s,789,474,track('MISSILE WARNING'),18,DIM)
    app.missile_toggle=Toggle(s,736,539,track('MISSILE OCR'),app.toggle_missile_enabled)
    app.ai_toggle=Toggle(s,736,589,track('MISSILE AI'),app.toggle_ai_enabled)
    app.ai_assist_toggle=Toggle(s,736,640,track('AI ASSIST'),app.toggle_ai_assist)
    app.test_flare_btn=Button(s,(1184,517,1434,564),track('TEST FLARE'),app.test_flare,17,text_x=1322)
    app.false_alarm_btn=Button(s,(1184,614,1434,662),track('FALSE ALARM'),app.ai_false_alarm,15,text_x=1337)
    app.ai_status_label=StatusLabel(s,1309,589,'AI unavailable' if app.learner is None else 'collecting · 0 samples',17,DIM,'center',bold=True,width=250)
    Label(s,187,758,track('ALERT BELOW'),17,DIM)
    app.threshold_label=Label(s,142,813,'--',49,WHITE,bold=True)
    Label(s,481,758,track('OVERLAY'),17,DIM)
    app.position_label=Label(s,467,813,'Center Low',29,WHITE,bold=True)
    Label(s,789,716,track('HOTKEYS'),18,DIM)
    app.hotkey_edit_btn=Button(s,(1301,702,1434,750),track('EDIT'),app.open_hotkey_settings,17,text_x=1390)
    app.hotkey_label=Hotkeys(s,app,module)
    Button(s,(705,889,1023,942),track('RESET TO DEFAULTS'),app.reset_to_defaults,17,text_x=896)
    Button(s,(1280,889,1453,942),track('EXIT'),app.on_close,17,text_x=1398)
    Button(s,(1041,889,1261,942),track('UPDATES'),lambda: app.updater.open_settings(),17,style='glass')
    Label(s,1122,105,track('V'+module.__version__),19,DIM,anchor='e',bold=True)
    app.update_status_label=Label(s,1000,105,'',12,DIM,anchor='e')
    s.ready=True
    app.root.update_idletasks()
    s.layout()
