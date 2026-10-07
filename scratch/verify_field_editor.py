import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app

def test_editor_dom():
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['user_id'] = 1
            sess['email'] = 'admin@example.com'
            sess['role'] = 'admin'
            sess['is_admin'] = True

        res = client.get('/admin/approval-management?mode=form-builder')
        assert res.status_code == 200, f'Status {res.status_code}'
        html = res.data.decode('utf-8')
        assert 'id="modal-field-editor"' in html, 'modal-field-editor missing'
        assert 'id="form-field-editor"' in html, 'form-field-editor missing'
        assert 'id="fe-label"' in html, 'fe-label missing'
        assert 'id="fe-name"' in html, 'fe-name missing'
        assert 'id="fe-type"' in html, 'fe-type missing'
        assert 'id="fe-required"' in html, 'fe-required missing'
        assert 'id="fe-options"' in html, 'fe-options missing'
        assert 'id="btn-add-form-field"' in html, 'btn-add-form-field missing'
        assert 'id="btn-save-form-config"' in html, 'btn-save-form-config missing'
        
        assign_idx = html.find('id="modal-assign-approver"')
        editor_idx = html.find('id="modal-field-editor"')
        assert assign_idx != -1 and editor_idx != -1
        assign_block = html[assign_idx:editor_idx]
        
        open_divs = assign_block.count('<div')
        close_divs = assign_block.count('</div>')
        print(f'open_divs in modal-assign-approver: {open_divs}, close_divs: {close_divs}')
        assert open_divs == close_divs, 'modal-assign-approver divs are not balanced before modal-field-editor'
        print('[OK] modal-field-editor is completely independent and correctly placed in the DOM!')

        # Verify workflow JSON loading
        res_wf = client.get('/admin/approval-workflows/1/json')
        assert res_wf.status_code == 200, f'Status {res_wf.status_code}'
        data = res_wf.get_json()
        assert data.get('success') is True
        print('[OK] /admin/approval-workflows/1/json returned active workflow data')

        # Test updating schema with various field types
        new_schema = {
            "enabled": True,
            "title": "Comprehensive Test Form",
            "description": "Form with all field types",
            "fields": [
                {"id": "f_1", "name": "field_text", "label": "Full Name", "type": "text", "required": True, "width": "half"},
                {"id": "f_2", "name": "field_num", "label": "Age", "type": "number", "required": False, "width": "half", "min": 18, "max": 70},
                {"id": "f_3", "name": "field_date", "label": "Arrival Date", "type": "date", "required": True, "width": "half"},
                {"id": "f_4", "name": "field_time", "label": "Arrival Time", "type": "time", "required": False, "width": "half"},
                {"id": "f_5", "name": "field_sel", "label": "Department", "type": "select", "required": True, "width": "half", "options": ["HR", "IT", "Ops"]},
                {"id": "f_6", "name": "field_chk", "label": "NDA Signed", "type": "checkbox", "required": False, "width": "half"},
                {"id": "f_7", "name": "field_area", "label": "Reason", "type": "textarea", "required": True, "width": "full"}
            ]
        }
        res_save = client.post('/admin/approval-workflows/1/form-schema', json={"form_schema": new_schema})
        assert res_save.status_code == 200, f'Status {res_save.status_code}'
        saved_data = res_save.get_json()
        assert saved_data.get('success') is True
        assert len(saved_data.get('form_schema', {}).get('fields', [])) == 7
        print('[OK] Saved dynamic form schema with 7 fields of diverse types')

if __name__ == '__main__':
    test_editor_dom()
    print('ALL FIELD EDITOR AND SCHEMA VERIFICATIONS COMPLETED SUCCESSFULLY!')
