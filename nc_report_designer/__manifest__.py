{
    'name': 'Report Designer',
    'version': '19.0.1.7.1',
    'category': 'Productivity',
    'summary': 'Drag-and-drop, edit-while-previewing designer for printed (QWeb) reports',
    'description': """
Edit printed reports on a live preview of a real record:

* click any element of the report to select it, double-click text to edit it in place
* drag fields of the printed record (and of its lines) onto the page, or blocks
  (text, headings, columns, tables, dividers, page breaks, ...)
* move, duplicate, delete elements; change styles, conditions, field widgets and
  attributes; edit the XML of any element
* edit (name, Print menu per company), duplicate (a copy gets its own templates) and delete
  copies of reports; choose the report attached to each email template
* undo / redo, a dated list of every change to look at the report as it was at any\n  point and go back to it, and reset a template to its original
* a master report: the companies it applies to, and the report used for the others
  (when printing, or attached to an email)

Every change is saved as a regular inherited QWeb view (one per template, made of
``<xpath>`` specs), so the original templates stay untouched and module upgrades keep
applying.
""",
    'author': 'NC Automation',
    'license': 'LGPL-3',
    'depends': ['web', 'base_setup'],
    'data': [
        'security/ir.model.access.csv',
        'views/report_designer_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'nc_report_designer/static/src/designer/**/*',
        ],
    },
    'application': True,
}
