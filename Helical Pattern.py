import adsk.core as core
import adsk.fusion as fusion
from pathlib import Path
from .lib import fusionAddInUtils as futil
from . import config

DEBUG = config.DEBUG

# Initialize the global variables for the Application and UserInterface objects.
app: core.Application = core.Application.get()
ui: core.UserInterface = app.userInterface
tempBRepManager: fusion.TemporaryBRepManager = fusion.TemporaryBRepManager.get()

CMD_ID = f"{config.COMPANY_NAME}_{config.ADDIN_NAME}"
CMD_NAME = "Helical Pattern"
CMD_Description = "Creates a pattern that helicals around an axis."

EDIT_CMD_ID = f"{CMD_ID}_edit"
EDIT_CMD_NAME = f"Edit {CMD_NAME}"
EDIT_CMD_Description = f"{EDIT_CMD_NAME}."

# Specify that the command will be promoted to the panel.
IS_PROMOTED = False

WORKSPACE_ID = "FusionSolidEnvironment"
PANEL_ID = "SolidCreatePanel"
DROPDOWN_ID = "PatternDropDown"
COMMAND_BESIDE_ID = "PatternOnPath"

# Resource location for command icons, here we assume a sub folder in this directory named "resources".
ICON_FOLDER = str(Path(__file__).resolve().parent / "resources")

customFeatDef: fusion.CustomFeatureDefinition = None
editedFeat: fusion.CustomFeature = None

local_handlers = []


# Executed when add-in is run.
def run(context) -> None:
    futil.log(f"{CMD_NAME} is now running.")

    cmd_def: core.CommandDefinition = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)

    edit_cmd_def: core.CommandDefinition = ui.commandDefinitions.addButtonDefinition(EDIT_CMD_ID, EDIT_CMD_NAME, EDIT_CMD_Description, "")
    futil.add_handler(edit_cmd_def.commandCreated, edit_command_created)

    workspace: core.Workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel: core.ToolbarPanel = workspace.toolbarPanels.itemById(PANEL_ID)
    drop_down: core.DropDownControl = panel.controls.itemById(DROPDOWN_ID)
    control: core.CommandControl = drop_down.controls.addCommand(cmd_def, COMMAND_BESIDE_ID, False)
    control.isPromoted = IS_PROMOTED

    global customFeatDef
    customFeatDef = fusion.CustomFeatureDefinition.create("helicalPattern", "Helical Pattern", ICON_FOLDER)
    customFeatDef.editCommandId = EDIT_CMD_ID


# Executed when add-in is stopped.
def stop(context) -> None:
    futil.log(f"{CMD_NAME} stopped running.")

    futil.clear_handlers()

    workspace: core.Workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel: core.ToolbarPanel = workspace.toolbarPanels.itemById(PANEL_ID)
    drop_down: core.DropDownControl = panel.controls.itemById(DROPDOWN_ID)
    control: core.CommandControl = drop_down.controls.itemById(CMD_ID)
    if control:
        control.deleteMe()

    cmd_def: core.CommandDefinition = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()

    edit_cmd_def: core.CommandDefinition = ui.commandDefinitions.itemById(EDIT_CMD_ID)
    if edit_cmd_def:
        edit_cmd_def.deleteMe()


# Function that is called when a user clicks the corresponding button in the UI.
# This defines the contents of the command dialog and connects to the command related events.
def command_created(args: core.CommandCreatedEventArgs) -> None:
    futil.log(f"{CMD_NAME} Command Created Event")

    inputs: core.CommandInputs = args.command.commandInputs
    design: fusion.Design = app.activeProduct
    defLenUnits = design.unitsManager.defaultLengthUnits

    objectType_input: core.DropDownCommandInput = inputs.addDropDownCommandInput("objectType", "Object Type", 1)
    objectTypes: list[str] = ["Bodies", "Faces"]
    objectType_input.maxVisibleItems = len(objectTypes)
    selected = True
    for list_item in objectTypes:
        objectType_input.listItems.add(list_item, selected, "")
        selected = False

    object_input: core.SelectionCommandInput = inputs.addSelectionInput("object", "Select Objects", "Select bodies or faces.")
    object_input.hasFocus = True
    object_input.setSelectionLimits(1)
    object_input.addSelectionFilter(objectType_input.selectedItem.name)
    
    axis_input: core.SelectionCommandInput = inputs.addSelectionInput("axis", "Select Axis", "Select edge or axis of rotation")
    axis_input.hasFocus = False
    axis_input.setSelectionLimits(1,1)
    axis_input.selectionFilters = ["LinearEdges", "ConstructionLines", "CircularEdges", "CylindricalFaces"]

    inputs.addIntegerSpinnerCommandInput("quantity", "Quantity", 2, 100, 1, 3)

    inputs.addValueInput("angularSpacing", "Angular Spacing", "deg", core.ValueInput.createByString("30"))

    inputs.addValueInput("axialSpacing", "Axial Spacing", defLenUnits, core.ValueInput.createByReal(1))

    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


def getSelectedAxis(axisObj: fusion.BRepEdge | fusion.ConstructionAxis | fusion.BRepFace) -> tuple[core.Point3D, core.Vector3D]:
    axis = []
    if axisObj.objectType == fusion.BRepEdge.classType() or axisObj.objectType == fusion.ConstructionAxis.classType(): 
        if axisObj.geometry.objectType == core.Arc3D.classType() or axisObj.geometry.objectType == core.Circle3D.classType():
            axis.append(axisObj.geometry.center)
            axis.append(axisObj.geometry.normal)
        elif axisObj.geometry.objectType == core.InfiniteLine3D.classType():
            axis.append(axisObj.geometry.origin)
            axis.append(axisObj.geometry.direction)
        else:
            axis.append(axisObj.geometry.startPoint)
            axis.append(core.Vector3D.create(axisObj.geometry.endPoint.x - axisObj.geometry.startPoint.x,
                                                  axisObj.geometry.endPoint.y - axisObj.geometry.startPoint.y,
                                                  axisObj.geometry.endPoint.z - axisObj.geometry.startPoint.z))
    elif axisObj.objectType == fusion.BRepFace.classType():
        axis.append(axisObj.geometry.origin)
        axis.append(axisObj.geometry.axis)
    axis[1].normalize()
    return tuple(axis)


def getTranform(axis: tuple[core.Point3D, core.Vector3D], distance: float, angle: float) -> core.Matrix3D:
    transform = core.Matrix3D.create()
    moveMatrix = core.Matrix3D.create()

    translationVector: core.Vector3D = axis[1].copy()
    translationVector.scaleBy(distance) # Modify vector - define distatnce between each copy

    moveMatrix.translation = translationVector  # Move matrix for Distance

    transform.setToRotation(angle, axis[1], axis[0]) # Set Rotation for each component
    transform.transformBy(moveMatrix) # Transfrom matrix by distance matrix

    return transform


def createPatternElement(comp: fusion.Component, inputEntities: list[fusion.BRepBody], transform: core.Matrix3D) -> fusion.BaseFeature:
    baseFeat: fusion.BaseFeature = comp.features.baseFeatures.add()
    baseFeat.startEdit()
    for obj in inputEntities:
        newObj = tempBRepManager.copy(obj)
        tempBRepManager.transform(newObj, transform)
        comp.bRepBodies.add(newObj, baseFeat)
    baseFeat.finishEdit()
    return baseFeat


# This event handler is called when the user clicks the OK button in the command dialog or 
# is immediately called after the created event not command inputs were created for the dialog.
def command_execute(args: core.CommandEventArgs) -> None:
    futil.log(f"{CMD_NAME} Command Execute Event")

    # Get a reference to your command's inputs.
    inputs: core.CommandInputs = args.command.commandInputs

    objectType_input: core.DropDownCommandInput = inputs.itemById("objectType")
    object_input: core.SelectionCommandInput = inputs.itemById("object")
    axis_input: core.SelectionCommandInput = inputs.itemById("axis")
    quantity_input: core.IntegerSpinnerCommandInput = inputs.itemById("quantity")
    angularSpacing_input: core.ValueCommandInput = inputs.itemById("angularSpacing")
    axialSpacing_input: core.ValueCommandInput = inputs.itemById("axialSpacing")

    inputEntities: list[fusion.BRepBody] = []
    for i in range(object_input.selectionCount):
        inputEntities.append(object_input.selection(i).entity)

    axisObj = axis_input.selection(0).entity
    axis: tuple[core.Point3D, core.Vector3D] = getSelectedAxis(axisObj)

    design: fusion.Design = app.activeProduct
    rootComp: fusion.Component = design.rootComponent

    patternElements: list[fusion.BaseFeature] = []
    
    for i in range(1, quantity_input.value):
        transform: core.Matrix3D = getTranform(axis, axialSpacing_input.value * i, angularSpacing_input.value * i)
        baseFeat: fusion.BaseFeature = createPatternElement(rootComp, inputEntities, transform)
        patternElements.append(baseFeat)

    unitsManager = design.unitsManager
    defLengthUnits = unitsManager.defaultLengthUnits

    customFeatInput: fusion.CustomFeatureInput = rootComp.features.customFeatures.createInput(customFeatDef)

    quantityValueInput = core.ValueInput.createByString(str(quantity_input.value))
    customFeatInput.addCustomParameter("quantity", "Quantity", quantityValueInput, "")

    angularSpacingValueInput = core.ValueInput.createByString(angularSpacing_input.expression)
    customFeatInput.addCustomParameter("angularSpacing", "AngularSpacing", angularSpacingValueInput, "deg")

    axialSpacingValueInput = core.ValueInput.createByString(axialSpacing_input.expression)
    customFeatInput.addCustomParameter("axialSpacing", "AxialSpacing", axialSpacingValueInput, defLengthUnits)

    customFeatInput.addDependency("axis", axisObj)
    for i in range(len(inputEntities)):
        obj = inputEntities[i]
        customFeatInput.addDependency(f"obj_{i}", obj)

    customFeatInput.setStartAndEndFeatures(patternElements[0], patternElements[-1])
    customFeat: fusion.CustomFeature = rootComp.features.customFeatures.add(customFeatInput)
    customFeat.customNamedValues.addOrSetValue("objectType", objectType_input.selectedItem.name)


# This event handler is called when the command needs to compute a new preview in the graphics window.
def command_preview(args: core.CommandEventArgs) -> None:
    futil.log(f"{CMD_NAME} Command Preview Event")

    # Get a reference to your command's inputs.
    inputs: core.CommandInputs = args.command.commandInputs

    object_input: core.SelectionCommandInput = inputs.itemById("object")
    axis_input: core.SelectionCommandInput = inputs.itemById("axis")
    quantity_input: core.IntegerSpinnerCommandInput = inputs.itemById("quantity")
    angularSpacing_input: core.ValueCommandInput = inputs.itemById("angularSpacing")
    axialSpacing_input: core.ValueCommandInput = inputs.itemById("axialSpacing")

    inputEntities: list[fusion.BRepBody] = []
    for i in range(object_input.selectionCount):
        inputEntities.append(object_input.selection(i).entity)

    axisObj = axis_input.selection(0).entity
    axis: tuple[core.Point3D, core.Vector3D] = getSelectedAxis(axisObj)

    design: fusion.Design = app.activeProduct
    rootComp: fusion.Component = design.rootComponent

    if rootComp.customGraphicsGroups.count > 0:
        rootComp.customGraphicsGroups.item(0).deleteMe()
        app.activeViewport.refresh()

    graphics: fusion.CustomGraphicsGroup = rootComp.customGraphicsGroups.add()
    color: core.Color = core.Color.create(120, 255, 23, 90)
    graphics.color = fusion.CustomGraphicsSolidColorEffect.create(color)

    patternElements: list[fusion.BaseFeature] = []
    
    for i in range(1, quantity_input.value):
        transform: core.Matrix3D = getTranform(axis, axialSpacing_input.value * i, angularSpacing_input.value * i)
        for obj in inputEntities:
            newObj = tempBRepManager.copy(obj)
            tempBRepManager.transform(newObj, transform)
            graphics.addBRepBody(newObj)

    app.activeViewport.refresh()


# This event handler is called when the user changes anything in the command dialog
# allowing you to modify values of other inputs based on that change.
def command_input_changed(args: core.InputChangedEventArgs) -> None:
    changed_input = args.input
    inputs: core.CommandInputs = args.inputs

    futil.log(f"{CMD_NAME} Input Changed Event fired from a change to {changed_input.id}")
    
    objectType_input: core.DropDownCommandInput = inputs.itemById("objectType")
    object_input: core.SelectionCommandInput = inputs.itemById("object")
    # axis_input: core.SelectionCommandInput = inputs.itemById("axis")
    # quantity_input: core.IntegerSpinnerCommandInput = inputs.itemById("quantity")
    # angularSpacing_input: core.AngleValueCommandInput = inputs.itemById("angularSpacing")
    # axialSpacing_input: core.ValueCommandInput = inputs.itemById("axialSpacing")

    if changed_input.id == objectType_input.id:
        object_input.clearSelection()
        object_input.clearSelectionFilter()
        object_input.addSelectionFilter(objectType_input.selectedItem.name)


# This event handler is called when the user interacts with any of the inputs in the dialog
# which allows you to verify that all of the inputs are valid and enables the OK button.
def command_validate_input(args: core.ValidateInputsEventArgs) -> None:
    # futil.log(f"{CMD_NAME} Validate Input Event")
    # inputs: core.CommandInputs = args.inputs
    pass


# This event handler is called when the command terminates.
def command_destroy(args: core.CommandEventArgs) -> None:
    futil.log(f"{CMD_NAME} Command Destroy Event")

    global local_handlers
    local_handlers = []


def edit_command_created(args: core.CommandCreatedEventArgs) -> None:
    futil.log(f"{CMD_NAME} Edit Command Created Event")

    inputs: core.CommandInputs = args.command.commandInputs
    design: fusion.Design = app.activeProduct
    defLenUnits = design.unitsManager.defaultLengthUnits

    global editedFeat
    editedFeat = ui.activeSelections.item(0).entity
    if not editedFeat:
        futil.log("No feature found")
        return

    params: fusion.CustomFeatureParameters = editedFeat.parameters

    selectedObjectType = editedFeat.customNamedValues.value("objectType")
    objectType_input: core.DropDownCommandInput = inputs.addDropDownCommandInput("objectType", "Object Type", 1)
    objectTypes: list[str] = ["Bodies", "Faces"]
    objectType_input.maxVisibleItems = len(objectTypes)
    for objType in objectTypes:
        selected = True if objType == selectedObjectType else False
        objectType_input.listItems.add(objType, selected, "")

    object_input: core.SelectionCommandInput = inputs.addSelectionInput("object", "Select Objects", "Select bodies or faces.")
    object_input.hasFocus = True
    object_input.setSelectionLimits(1)
    object_input.addSelectionFilter(objectType_input.selectedItem.name)
    
    axis_input: core.SelectionCommandInput = inputs.addSelectionInput("axis", "Select Axis", "Select edge or axis of rotation")
    axis_input.hasFocus = False
    axis_input.setSelectionLimits(1,1)
    axis_input.selectionFilters = ["LinearEdges", "ConstructionLines", "CircularEdges", "CylindricalFaces"]

    quantity = int(params.itemById("quantity").value)
    inputs.addIntegerSpinnerCommandInput("quantity", "Quantity", 2, 100, 1, quantity)

    angleValueInput = core.ValueInput.createByReal(params.itemById("angularSpacing").value)
    inputs.addValueInput("angularSpacing", "Angular Spacing", "deg", angleValueInput)

    distanceValueInput = core.ValueInput.createByReal(params.itemById("axialSpacing").value)
    inputs.addValueInput("axialSpacing", "Axial Spacing", defLenUnits, distanceValueInput)

    futil.add_handler(args.command.activate, edit_command_activate, local_handlers=local_handlers)
    futil.add_handler(args.command.execute, edit_command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


def edit_command_activate(args: core.CommandEventArgs) -> None:
    futil.log(f"{CMD_NAME} Edit Command Activate Event")

    design: fusion.Design = app.activeProduct
    timeline = design.timeline

    global restoreTimelineObj, isRolledForEdit
    restoreTimelineObj = timeline.item(timeline.markerPosition - 1)
    editedFeat.timelineObject.rollTo(True)
    isRolledForEdit = True
    args.command.beginStep()

    inputs: core.CommandInputs = args.command.commandInputs

    object_input: core.SelectionCommandInput = inputs.itemById("object")
    for i in range(editedFeat.dependencies.count - 1):
        object_input.addSelection(editedFeat.dependencies.itemById(f"obj_{i}").entity)

    axis_input: core.SelectionCommandInput = inputs.itemById("axis")
    axis_input.addSelection(editedFeat.dependencies.itemById("axis").entity)


def edit_command_execute(args: core.CommandEventArgs) -> None:
    futil.log(f"{CMD_NAME} Edit Command Execute Event")

    inputs: core.CommandInputs = args.command.commandInputs
    design: fusion.Design = app.activeProduct
    rootComp: fusion.Component = design.rootComponent

    global editedFeat
    objectType_input: core.DropDownCommandInput = inputs.itemById("objectType")
    editedFeat.customNamedValues.addOrSetValue("objectType", objectType_input.selectedItem.name)

    object_input: core.SelectionCommandInput = inputs.itemById("object")
    futil.log(f"{object_input.id}: {object_input.isValid}")
    futil.log(f"Selection Count: {object_input.selectionCount}")

    inputEntities: list[fusion.BRepBody] = []
    for i in range(object_input.selectionCount):
        inputEntities.append(object_input.selection(i).entity)

    axis_input: core.SelectionCommandInput = inputs.itemById("axis")
    axisObj = axis_input.selection(0).entity
    axis: tuple[core.Point3D, core.Vector3D] = getSelectedAxis(axisObj)

    quantity_input: core.IntegerSpinnerCommandInput = inputs.itemById("quantity")
    angularSpacing_input: core.ValueCommandInput = inputs.itemById("angularSpacing")
    axialSpacing_input: core.ValueCommandInput = inputs.itemById("axialSpacing")

    if len(editedFeat.features) > quantity_input.value - 1:
        for _ in range(len(editedFeat.features) - (quantity_input.value - 1)):
            feat: fusion.Feature = editedFeat.features.pop()
            feat.deleteMe()

    patternElements: list[fusion.BaseFeature] = editedFeat.features
    for feat in patternElements:
        if len(feat.sourceBodies) > len(inputEntities):
            body: fusion.BRepBody = feat.sourceBodies.pop()
            feat.startEdit()
            body.deleteMe()
            feat.finishEdit()

    for i in range(1, quantity_input.value):
        transform: core.Matrix3D = getTranform(axis, axialSpacing_input.value * i, angularSpacing_input.value * i)

        if i > len(patternElements):
            editedFeat.timelineObject.rollTo(False)
            baseFeat: fusion.BaseFeature = createPatternElement(rootComp, inputEntities, transform)
            patternElements.append(baseFeat)
        else:
            baseFeat: fusion.BaseFeature = patternElements[i - 1]
            baseFeat.startEdit()
            for j in range(baseFeat.bodies.count):
                sourceBody: fusion.BRepBody = baseFeat.bodies.item(j)
                newBody = tempBRepManager.copy(inputEntities[j])
                tempBRepManager.transform(newBody, transform)
                baseFeat.updateBody(sourceBody, newBody)
            baseFeat.finishEdit()

    editedFeat.dependencies.deleteAll()
    editedFeat.dependencies.add("axis", axisObj)
    for i in range(len(inputEntities)):
        editedFeat.dependencies.add(f"obj_{i}", inputEntities[i])

    editedFeat.dependencies.itemById("axis").entity = axisObj
    editedFeat.parameters.itemById("quantity").value = quantity_input.value
    editedFeat.parameters.itemById("angularSpacing").expression = angularSpacing_input.expression
    editedFeat.parameters.itemById("axialSpacing").expression = axialSpacing_input.expression

    editedFeat.timelineObject.rollTo(True)
    editedFeat.setStartAndEndFeatures(patternElements[0], patternElements[-1])
    
    global isRolledForEdit
    if isRolledForEdit:
        restoreTimelineObj.rollTo(False)
        isRolledForEdit = False

    editedFeat = None