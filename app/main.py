
import csv, os, sys, time
from dataclasses import dataclass
from typing import Optional
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QLineEdit,QPushButton,QTableWidget,QTableWidgetItem,QMessageBox,QSpinBox,QTextEdit,QGroupBox,QCheckBox,QFileDialog,QTabWidget,QComboBox
try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill
except Exception:
    Workbook=load_workbook=None
try:
    from pymodbus.client import ModbusTcpClient
except Exception:
    ModbusTcpClient=None
BRAND_BLUE='#008CD7'; BRAND_BLUE_DARK='#005C8E'; BG='#EFEFEF'; CARD='#FFFFFF'; TEXT='#3C3C3C'
CATEGORIES=['Drive Identification','Communication','Command and Reference','Monitoring','Faults and Diagnostics','Motor Setup','Application Functions','Protection and Limits','Ramp and Motion Profile','Input Output Configuration','Maintenance and Service']
def resource_path(p): return os.path.join(getattr(sys,'_MEIPASS',os.path.abspath(os.path.join(os.path.dirname(__file__),'..'))),p)
def sf(v,d=0.0):
    try: return d if v is None or str(v).strip()=='' else float(v)
    except Exception: return d
def si(v,d=0):
    try: return int(float(v))
    except Exception: return d
@dataclass
class Parameter:
    model:str; reference:str; code:str; name:str; address:int; datatype:str; scale:float; default:float; min:float; max:float; unit:str; access:str; monitor:bool; write_protect:bool=False; scope:bool=False; group:str='Monitoring'; subcategory:str=''; functional_area:str=''; safety_class:str=''; write_policy:str=''; display_order:int=9999; notes:str=''; value:Optional[float]=None; online_value:Optional[float]=None; user_modified:bool=False
    @property
    def effective_value(self): return self.value if self.value is not None else self.default
class ParameterDB:
    def __init__(self): self.params=[]
    def _bool(self,v): return str(v or 'FALSE').upper() in ('TRUE','1','YES','Y')
    def load_csv(self,path):
        self.params=[]
        with open(path,newline='',encoding='utf-8-sig') as f:
            for r in csv.DictReader(f): self.add_row(r)
    def add_row(self,r):
        group=(r.get('group') or 'Monitoring').strip()
        if group not in CATEGORIES: group='Monitoring'
        self.params.append(Parameter((r.get('model') or 'XD4000').strip(),(r.get('reference') or 'ALL').strip(),(r.get('code') or '').strip(),(r.get('name') or '').strip(),si(r.get('address')),(r.get('datatype') or 'uint16').strip().lower(),sf(r.get('scale'),1),sf(r.get('default')),sf(r.get('min'),-32768),sf(r.get('max'),65535),(r.get('unit') or '').strip(),(r.get('access') or 'RO').strip().upper(),self._bool(r.get('monitor')),self._bool(r.get('write_protect')),self._bool(r.get('scope')),group,(r.get('subcategory') or '').strip(),(r.get('functional_area') or '').strip(),(r.get('safety_class') or '').strip(),(r.get('write_policy') or '').strip(),si(r.get('display_order'),9999),(r.get('notes') or '').strip()))
    def filtered(self,search='',monitor_only=False,group='All'):
        s=(search or '').lower().strip(); out=[]
        for p in sorted(self.params,key=lambda x:(x.display_order,x.code)):
            if monitor_only and not p.monitor: continue
            if group!='All' and p.group!=group: continue
            if s and not (s in p.code.lower() or s in p.name.lower() or s in str(p.address) or s in p.group.lower()): continue
            out.append(p)
        return out
    def by_code(self,code):
        for p in self.params:
            if p.code.upper()==str(code).upper(): return p
        return None
class ModbusGateway:
    def __init__(self): self.client=None; self.unit_id=1; self.address_offset=0
    def connect_tcp(self,host,port,unit_id,zero_based=False):
        if ModbusTcpClient is None: raise RuntimeError('pymodbus is not installed')
        self.unit_id=unit_id; self.address_offset=-1 if zero_based else 0; self.client=ModbusTcpClient(host=host,port=port,timeout=3)
        if not self.client.connect(): raise RuntimeError('Could not connect to Modbus TCP device')
    def close(self):
        if self.client: self.client.close()
        self.client=None
    def is_connected(self): return self.client is not None
    def _addr(self,a):
        a=a+self.address_offset
        if a<0: raise RuntimeError(f'Invalid address after offset: {a}')
        return a
    def _kwargs(self): return [{'slave':self.unit_id},{'unit':self.unit_id},{'device_id':self.unit_id},{}]
    def read_registers(self,address,count=1):
        address=self._addr(address); last=None
        for kw in self._kwargs():
            try:
                rr=self.client.read_holding_registers(address=address,count=count,**kw)
                if rr.isError(): raise RuntimeError(str(rr))
                return rr.registers
            except TypeError as e: last=e; continue
        raise RuntimeError(f'read_holding_registers API failed: {last}')
    def write_register(self,address,value):
        address=self._addr(address); last=None
        for kw in self._kwargs():
            try:
                wr=self.client.write_register(address=address,value=value,**kw)
                if wr.isError(): raise RuntimeError(str(wr))
                return
            except TypeError as e: last=e; continue
            except Exception as e: last=e; break
        for kw in self._kwargs():
            try:
                wr=self.client.write_registers(address=address,values=[value],**kw)
                if wr.isError(): raise RuntimeError(str(wr))
                return
            except TypeError as e: last=e; continue
            except Exception as e: last=e; break
        raise RuntimeError(f'Write failed using FC06 and FC16: {last}')
    def read_param(self,p):
        regs=self.read_registers(p.address,2 if p.datatype in ('uint32','int32') else 1)
        if p.datatype=='int16': raw=regs[0] if regs[0]<32768 else regs[0]-65536
        elif p.datatype=='uint32': raw=(regs[0]<<16)+regs[1]
        elif p.datatype=='int32':
            raw=(regs[0]<<16)+regs[1]
            if raw>=2147483648: raw-=4294967296
        else: raw=regs[0]
        return raw*p.scale
    def write_param(self,p,val):
        if p.access!='RW': raise RuntimeError(f'{p.code} is read-only')
        raw=int(round(val/(p.scale if p.scale else 1)))
        if p.datatype=='int16' and raw<0: raw=65536+raw
        self.write_register(p.address,raw & 0xFFFF)
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('LK XD4000 Phase-5C Full Parameter Integration'); self.resize(1540,900)
        self.db=ParameterDB(); self.gateway=ModbusGateway(); self.params=[]; self.keepalive_timer=QTimer(self); self.keepalive_timer.timeout.connect(self.keepalive_tick)
        self.build_ui(); self.apply_theme(); self.load_db()
    def apply_theme(self):
        self.setStyleSheet(f"""QMainWindow {{background:{BG}; font-family:Roboto, Segoe UI, sans-serif; color:{TEXT};}} QGroupBox {{background:{CARD}; border:1px solid #C1C1C1; border-radius:10px; margin-top:12px; padding:10px; font-weight:bold;}} QPushButton {{background:{BRAND_BLUE}; color:white; border:none; border-radius:8px; min-height:35px; padding:6px 12px; font-weight:bold;}} QPushButton:hover {{background:{BRAND_BLUE_DARK};}} QLineEdit,QSpinBox,QComboBox {{background:#FFF; border:1px solid #C1C1C1; border-radius:6px; padding:5px; min-height:24px;}} QTableWidget {{background:#FFF;}} QTextEdit {{background:#FFF; border:1px solid #C1C1C1; border-radius:8px; padding:6px;}}""")
    def log(self,m): self.logbox.append(f'[{time.strftime("%H:%M:%S")}] {m}')
    def load_db(self): self.db.load_csv(resource_path(os.path.join('data','xd4000_phase5c_parameters.csv'))); self.refresh_params(); self.log(f'Loaded Phase-5C parameter database: {len(self.db.params)} parameters')
    def build_ui(self):
        c=QWidget(); self.setCentralWidget(c); root=QVBoxLayout(c)
        box=QGroupBox('XD4000 Full Parameter Integration - Modbus TCP'); g=QGridLayout(box)
        self.host=QLineEdit('192.168.1.10'); self.port=QSpinBox(); self.port.setRange(1,65535); self.port.setValue(502); self.unit=QSpinBox(); self.unit.setRange(1,255); self.unit.setValue(1)
        self.zero=QCheckBox('Use zero-based address (-1)'); self.search=QLineEdit(); self.search.setPlaceholderText('Search code/name/address/group'); self.search.textChanged.connect(self.refresh_params); self.group_filter=QComboBox(); self.group_filter.addItems(['All']+CATEGORIES); self.group_filter.currentTextChanged.connect(self.refresh_params); self.mononly=QCheckBox('Monitor only'); self.mononly.stateChanged.connect(self.refresh_params)
        widgets=[('Drive IP',self.host),('TCP Port',self.port),('Unit ID',self.unit),('Address',self.zero),('Search',self.search),('Group',self.group_filter),('Filter',self.mononly)]
        for i,(lab,w) in enumerate(widgets): g.addWidget(QLabel(lab),0,i); g.addWidget(w,1,i)
        actions=[('Connect',self.connect_drive),('Disconnect',self.disconnect_drive),('Upload visible',self.upload_visible),('Download selected row',self.download_selected),('Export project Excel',self.export_project_excel),('Import project Excel/full list',self.import_project_excel),('Export Event Log',self.export_log)]
        for i,(txt,fn) in enumerate(actions): b=QPushButton(txt); b.clicked.connect(fn); g.addWidget(b,2,i)
        root.addWidget(box); self.tabs=QTabWidget(); root.addWidget(self.tabs,1); self.table=QTableWidget(); self.tabs.addTab(self.table,'Parameters')
        self.diagbox=QTextEdit(); self.diagbox.setReadOnly(True); self.tabs.addTab(self.diagbox,'Diagnostics / Dry-Run')
        self.logbox=QTextEdit(); self.logbox.setReadOnly(True); self.tabs.addTab(self.logbox,'Event Log')
    def refresh_params(self): self.params=self.db.filtered(self.search.text() if hasattr(self,'search') else '', self.mononly.isChecked() if hasattr(self,'mononly') else False, self.group_filter.currentText() if hasattr(self,'group_filter') else 'All'); self.populate()
    def fmt(self,v):
        if v=='': return ''
        try: return f'{float(v):.3f}'.rstrip('0').rstrip('.')
        except Exception: return str(v)
    def populate(self):
        heads=['Code','Group','Subcategory','Name','Address','Type','Scale','Offline Value','Online Value','Unit','Access','Protect','Policy','Notes']; self.table.blockSignals(True); self.table.setColumnCount(len(heads)); self.table.setHorizontalHeaderLabels(heads); self.table.setRowCount(len(self.params))
        for r,p in enumerate(self.params):
            vals=[p.code,p.group,p.subcategory,p.name,p.address,p.datatype,p.scale,p.effective_value,'' if p.online_value is None else self.fmt(p.online_value),p.unit,p.access,'Yes' if p.write_protect else 'No',p.write_policy,p.notes]
            for col,v in enumerate(vals):
                it=QTableWidgetItem(str(v)); it.setFlags((it.flags()|Qt.ItemIsEditable) if col==7 and p.access=='RW' and not p.write_protect else (it.flags()&~Qt.ItemIsEditable)); self.table.setItem(r,col,it)
        self.table.blockSignals(False)
        try: self.table.itemChanged.disconnect()
        except Exception: pass
        self.table.itemChanged.connect(self.on_edit); self.table.resizeColumnsToContents()
    def on_edit(self,item):
        if item.column()!=7 or item.row()>=len(self.params): return
        p=self.params[item.row()]
        try:
            val=float(item.text());
            if not(p.min<=val<=p.max): raise ValueError(f'Allowed range: {p.min} to {p.max}')
            p.value=val; p.user_modified=True; self.log(f'Offline value changed: {p.code} = {val}')
        except Exception as e: QMessageBox.warning(self,'Invalid value',str(e))
    def connect_drive(self):
        try: self.gateway.connect_tcp(self.host.text().strip(),self.port.value(),self.unit.value(),self.zero.isChecked()); self.log('Connected successfully')
        except Exception as e: self.log(f'Connection failed: {e}'); QMessageBox.critical(self,'Connection failed',str(e))
    def disconnect_drive(self): self.gateway.close(); self.log('Disconnected')
    def upload_visible(self):
        if not self.gateway.is_connected(): QMessageBox.warning(self,'Not connected','Connect first'); return
        ok=fail=0
        for p in self.params:
            try: p.online_value=self.gateway.read_param(p); p.value=p.online_value if not p.user_modified else p.value; ok+=1; self.log(f'Upload OK {p.code}@{p.address}={self.fmt(p.online_value)} {p.unit}')
            except Exception as e: fail+=1; self.log(f'Upload failed {p.code}@{p.address}: {e}')
        self.populate(); self.log(f'Upload complete. OK={ok}, Failed={fail}')
    def download_selected(self):
        if not self.gateway.is_connected(): QMessageBox.warning(self,'Not connected','Connect first'); return
        row=self.table.currentRow();
        if row<0 or row>=len(self.params): QMessageBox.warning(self,'No row selected','Select one row'); return
        p=self.params[row]
        if p.access!='RW': QMessageBox.warning(self,'Read-only',f'{p.code} is read-only'); return
        if p.write_protect: QMessageBox.warning(self,'Protected',f'{p.code} is write-protected'); return
        if QMessageBox.question(self,'Confirm write',f'Write {p.code}@{p.address}={p.effective_value} {p.unit}?')!=QMessageBox.Yes: return
        try: self.gateway.write_param(p,p.effective_value); rb=self.gateway.read_param(p); p.online_value=rb; p.value=rb; p.user_modified=False; self.populate(); self.log(f'Download/readback OK {p.code}@{p.address}={self.fmt(rb)} {p.unit}')
        except Exception as e: self.log(f'Download failed {p.code}: {e}')
    def keepalive_tick(self): pass
    def export_project_excel(self):
        if Workbook is None: QMessageBox.warning(self,'Missing dependency','openpyxl not installed'); return
        path,_=QFileDialog.getSaveFileName(self,'Export project Excel','XD4000_Phase5C_Project.xlsx','Excel Files (*.xlsx)')
        if not path: return
        wb=Workbook(); ws=wb.active; ws.title='Parameters'; headers=['code','name','address','datatype','scale','default','offline_value','online_value','unit','access','write_protect','group','subcategory','functional_area','safety_class','write_policy','display_order','notes']; ws.append(headers)
        for p in self.db.params: ws.append([p.code,p.name,p.address,p.datatype,p.scale,p.default,p.effective_value,'' if p.online_value is None else p.online_value,p.unit,p.access,p.write_protect,p.group,p.subcategory,p.functional_area,p.safety_class,p.write_policy,p.display_order,p.notes])
        cat=wb.create_sheet('CategoryMaster'); cat.append(['group']); [cat.append([c]) for c in CATEGORIES]
        imp=wb.create_sheet('FullParameterImport'); imp.append(headers)
        for cell in ws[1]: cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='008CD7')
        wb.save(path); self.log(f'Project Excel exported: {path}')
    def import_project_excel(self):
        path,_=QFileDialog.getOpenFileName(self,'Import project Excel/full list','','Excel/CSV Files (*.xlsx *.csv)')
        if not path: return
        imported=0
        if path.lower().endswith('.csv'):
            with open(path,newline='',encoding='utf-8-sig') as f:
                rows=list(csv.DictReader(f))
        else:
            if load_workbook is None: QMessageBox.warning(self,'Missing dependency','openpyxl not installed'); return
            wb=load_workbook(path,data_only=True); sheet='FullParameterImport' if 'FullParameterImport' in wb.sheetnames and wb['FullParameterImport'].max_row>1 else 'Parameters'
            ws=wb[sheet]; headers=[c.value for c in ws[1]]; rows=[]
            for row in ws.iter_rows(min_row=2,values_only=True): rows.append({headers[i]:row[i] for i in range(len(headers))})
        for r in rows:
            code=r.get('code') or r.get('Code')
            if not code: continue
            p=self.db.by_code(code)
            if p:
                if r.get('offline_value') not in (None,''):
                    try: p.value=float(r.get('offline_value')); p.user_modified=True
                    except Exception: pass
            else:
                rr={k:str(v) if v is not None else '' for k,v in r.items()}; rr.setdefault('model','XD4000'); rr.setdefault('reference','ALL'); rr.setdefault('default','0'); rr.setdefault('min','0'); rr.setdefault('max','65535'); rr.setdefault('access','RO'); rr.setdefault('monitor','TRUE'); rr.setdefault('write_protect','TRUE'); rr.setdefault('scope','FALSE'); rr.setdefault('group','Monitoring')
                self.db.add_row(rr); imported+=1
        self.refresh_params(); self.log(f'Project/full parameter import complete. New parameters added: {imported}')
    def export_log(self):
        path,_=QFileDialog.getSaveFileName(self,'Export event log','xd4000_event_log.txt','Text Files (*.txt)')
        if path: open(path,'w',encoding='utf-8').write(self.logbox.toPlainText()+'\n\n--- DIAGNOSTICS ---\n'+self.diagbox.toPlainText()); self.log(f'Event log exported: {path}')
if __name__=='__main__':
    app=QApplication(sys.argv); w=MainWindow(); w.show(); sys.exit(app.exec())
